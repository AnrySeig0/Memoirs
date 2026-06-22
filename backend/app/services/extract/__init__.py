"""Step 4: Extraction → grounded claims.

Every claim that leaves an extractor MUST cite ≥1 utterance ID from the
segment it was extracted from. `ExtractedClaim` enforces this at the
Pydantic layer (`source_utterance_ids` has `min_length=1`); the
repository (`insert_claim_with_sources`) re-enforces it before any DB
write.

`Extractor` is the protocol; `RuleExtractor` is a deterministic baseline
used in tests and as a safety floor; `LLMExtractor` is a stub for the
follow-up LLM integration (Instructor/vLLM per §5).
"""
from app.services.extract.base import Extractor
from app.services.extract.llm import LLMExtractor
from app.services.extract.rule import RULE_EXTRACTOR_CONFIDENCE, RuleExtractor
from app.services.extract.types import CANONICAL_CLAIM_TYPES, ExtractedClaim

__all__ = [
    "CANONICAL_CLAIM_TYPES",
    "Extractor",
    "ExtractedClaim",
    "LLMExtractor",
    "RULE_EXTRACTOR_CONFIDENCE",
    "RuleExtractor",
]
