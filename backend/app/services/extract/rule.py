"""Deterministic rule-based extractor.

This is NOT the production extractor. It exists for two reasons:

1. **Testable grounding contract.** The M2 acceptance test needs an
   extractor that always succeeds in tests without an LLM running. A
   regex-driven detector is reproducible bit-for-bit on any CI.
2. **A "least it can do" baseline.** If the LLM extractor goes down or
   produces garbage, the rule extractor still surfaces the obvious
   year-pinned facts. §9 says under-extract; this extractor is a clean
   floor on that.

It detects 4-digit years (19xx / 20xx) and emits one `fact` claim per
segment that contains one, grounded in the segment's source utterances.
That is the entire policy. No date arithmetic, no entity guessing.
"""
import re

from app.services.extract.types import ExtractedClaim
from app.services.segment.types import Segment

_YEAR_PATTERN = re.compile(r"\b(?:19|20)\d{2}\b")
RULE_EXTRACTOR_CONFIDENCE = 0.5


class RuleExtractor:
    """Year-mention detector. Emits at most one claim per segment."""

    def extract(self, segment: Segment) -> list[ExtractedClaim]:
        if not _YEAR_PATTERN.search(segment.text):
            return []
        normalised = " ".join(segment.text.split()).strip()
        if not normalised:
            return []
        return [
            ExtractedClaim(
                text=normalised,
                claim_type="fact",
                confidence=RULE_EXTRACTOR_CONFIDENCE,
                source_utterance_ids=list(segment.utterance_ids),
            )
        ]
