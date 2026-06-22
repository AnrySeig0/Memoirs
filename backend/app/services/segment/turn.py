"""Step 3: turn-level segmentation.

Identity policy for M2: each utterance becomes one segment, with offsets
preserved verbatim from the utterance row. This is the simplest setting
that still keeps the M2 grounding contract testable end-to-end.
"""
import uuid
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.services.segment.types import Segment
from app.db.models import Utterance


def segment_by_utterance(db: OrmSession, session_id: uuid.UUID) -> Iterator[Segment]:
    """Yield one Segment per utterance, ordered by char_start."""
    rows = (
        db.execute(
            select(Utterance)
            .where(Utterance.session_id == session_id)
            .order_by(Utterance.char_start)
        )
        .scalars()
        .all()
    )
    for row in rows:
        yield Segment(
            session_id=row.session_id,
            speaker=row.speaker,
            text=row.text,
            char_start=row.char_start,
            char_end=row.char_end,
            utterance_ids=(row.id,),
        )
