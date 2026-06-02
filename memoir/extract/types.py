"""Pydantic schema for grounded claims.

`ExtractedClaim` is the structured-output contract every extractor (rule,
LLM, future) must satisfy. The load-bearing field is
`source_utterance_ids`: a list with `min_length=1`, so any extractor —
or LLM through Instructor/Outlines — that emits an ungrounded claim is
rejected at the validation layer before the repository sees it.

`claim_type` is intentionally a `str` rather than an `Enum`, in line with
§9 "Không xây ontology phong phú vội — để cấu trúc tự nổi lên từ claim
loose". We provide a soft hint via `CANONICAL_CLAIM_TYPES` for prompts
and review UIs to use, but it is not enforced.
"""
import uuid

from pydantic import BaseModel, ConfigDict, Field

CANONICAL_CLAIM_TYPES: tuple[str, ...] = ("event", "fact", "relation", "trait", "other")


class ExtractedClaim(BaseModel):
    """One atomic claim with mandatory grounding."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1, description="The normalized claim sentence.")
    claim_type: str | None = Field(
        default=None,
        description=(
            "Loose category hint. Suggested vocabulary: "
            f"{', '.join(CANONICAL_CLAIM_TYPES)}. Free-form is fine."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Extractor's self-estimated confidence in [0,1].",
    )
    source_utterance_ids: list[uuid.UUID] = Field(
        min_length=1,
        description=(
            "Utterance IDs this claim is grounded in. MUST be non-empty — "
            "§4 hard rule, §9 'không đoán': an extractor that can't cite "
            "a source must under-extract instead of guessing."
        ),
    )
