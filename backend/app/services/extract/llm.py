"""LLM-driven extractor — Instructor-backed structured output.

Wiring per README §5:
    - OpenAI-compatible client → vLLM serving Qwen / Llama.
    - `instructor.from_openai(client)` so `response_model=list[ExtractedClaim]`
      is enforced at the SDK boundary.
    - Pydantic validation rejects ungrounded claims (§4): the schema's
      `source_utterance_ids` has `min_length=1`, so a hallucinated empty
      citation fails before it reaches the repository.

The system prompt encodes the §9 posture: under-extract on uncertainty,
do not resolve contradictions, output nothing if unsure.

Defaults — including `model`, `base_url`, `api_key`, `temperature` —
flow from `memoir.config.get_settings()` (which reads MEMOIR_LLM_*
env vars / .env). Explicit constructor args still win.
"""
import uuid

import instructor
from openai import OpenAI

from app.core.config import get_settings
from app.services.extract.types import CANONICAL_CLAIM_TYPES, ExtractedClaim
from app.services.segment.types import Segment

SYSTEM_PROMPT = (
    "You extract atomic claims from a single segment of a memoir interview.\n"
    "\n"
    "Hard rules (§4, §9):\n"
    "1. Every claim MUST cite at least one `source_utterance_id` taken "
    "verbatim from the list of utterance IDs provided with the segment. "
    "Never invent an ID. Never cite an ID that is not in the list.\n"
    "2. Under-extract on uncertainty. If you are not sure a claim is "
    "directly supported by the segment, do NOT emit it. Emitting nothing "
    "is always a valid answer.\n"
    "3. Output one atomic claim at a time — split compound statements.\n"
    "4. Do NOT resolve contradictions. If the segment says two things "
    "that conflict, emit both as separate claims; the editor will "
    "supersede later.\n"
    "5. `confidence` is your own estimate in [0, 1] of how strongly the "
    "segment supports the claim — not how plausible the claim is in "
    "general.\n"
    f"6. `claim_type` is a soft hint. Suggested vocabulary: "
    f"{', '.join(CANONICAL_CLAIM_TYPES)}. Free-form strings are fine; "
    "leave it null when unsure.\n"
    "\n"
    "Output: a JSON list of claims matching the ExtractedClaim schema. "
    "An empty list is the correct answer when the segment contains "
    "nothing you can confidently ground."
)


class LLMExtractor:
    """Instructor + OpenAI-compatible extractor.

    All defaults come from `memoir.config.Settings` (MEMOIR_LLM_* env
    vars or `.env`). Pass kwargs to override per instance — useful for
    tests and for the live-endpoint smoke check that needs a different
    base_url.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
    ) -> None:
        settings = get_settings()
        self.model = model or settings.llm_model
        self.base_url = base_url or settings.llm_base_url
        self.api_key = api_key or settings.llm_api_key
        self.temperature = (
            temperature if temperature is not None else settings.llm_temperature
        )
        self._client: instructor.Instructor | None = None

    def _get_client(self) -> instructor.Instructor:
        if self._client is None:
            self._client = instructor.from_openai(
                OpenAI(base_url=self.base_url, api_key=self.api_key)
            )
        return self._client

    def extract(self, segment: Segment) -> list[ExtractedClaim]:
        if not segment.utterance_ids or not segment.text.strip():
            return []
        allowed_ids = {uid for uid in segment.utterance_ids}
        user_prompt = (
            f"Segment text:\n---\n{segment.text}\n---\n\n"
            "Valid source_utterance_ids you may cite (cite at least one "
            "per claim, do not invent others):\n"
            + "\n".join(f"- {uid}" for uid in segment.utterance_ids)
        )
        client = self._get_client()
        claims: list[ExtractedClaim] = client.chat.completions.create(
            model=self.model,
            response_model=list[ExtractedClaim],
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        # Defensive: drop any claim that cites an ID outside the segment.
        # Pydantic enforces non-emptiness; segment-scope is enforced here.
        return [
            c for c in claims
            if c.source_utterance_ids
            and all(isinstance(uid, uuid.UUID) and uid in allowed_ids
                    for uid in c.source_utterance_ids)
        ]
