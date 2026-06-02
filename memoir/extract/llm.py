"""LLM-driven extractor — wired in a follow-up to M2.

Intended shape (per §5):
    - OpenAI-compatible client → vLLM serving Qwen/Llama.
    - `instructor.patch(client)` to force `response_model=list[ExtractedClaim]`.
    - System prompt explicitly instructs:
        * Output one atomic claim at a time.
        * Cite at least one source utterance ID from the segment, or
          output nothing.
        * Under-extract when unsure (§9).
        * Do not resolve contradictions; flag instead.

This module deliberately stays a stub in M2 so the milestone's acceptance
criterion (grounding contract) is provable without a model dependency.
"""
from memoir.extract.types import ExtractedClaim
from memoir.segment.types import Segment


class LLMExtractor:
    """Placeholder. Construction works; `extract` is the integration point."""

    def __init__(
        self,
        *,
        model: str = "qwen-plus",
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key

    def extract(self, segment: Segment) -> list[ExtractedClaim]:
        raise NotImplementedError(
            "LLMExtractor.extract is the post-M2 integration point. See "
            "memoir/extract/llm.py docstring for the intended wiring."
        )
