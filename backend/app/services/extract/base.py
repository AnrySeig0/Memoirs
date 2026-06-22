from typing import Protocol, runtime_checkable

from app.services.extract.types import ExtractedClaim
from app.services.segment.types import Segment


@runtime_checkable
class Extractor(Protocol):
    """Step 4 contract: segment → grounded claims.

    Implementations MUST cite at least one source utterance from the
    incoming segment for every claim they emit (enforced by
    `ExtractedClaim.source_utterance_ids` having `min_length=1`).
    Implementations SHOULD under-extract when uncertain, per §9.
    """

    def extract(self, segment: Segment) -> list[ExtractedClaim]: ...
