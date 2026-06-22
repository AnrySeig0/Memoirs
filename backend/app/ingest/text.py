"""Text-only ingestion: turns → append-only substrate.

Offsets are computed in Unicode codepoints (Python `len(str)`), which is
also how Postgres TEXT positions are counted for the purposes of this
project. The §5 lưu ý — "Kiểm tra offset không lệch khi xử lý Unicode có
dấu" — is exactly what `test_offsets.py` pins down.

Convention: the normalized session transcript is the concatenation of
turn texts joined with `\n`. Each utterance row records its half-open
range `[char_start, char_end)` against that string. With this rule,
`session_transcript[u.char_start:u.char_end] == u.text` holds for every
utterance, which is the property the provenance test (M6) relies on.
"""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session as OrmSession

from app.ingest.types import Turn
from app.store.repository import insert_session, insert_source, insert_utterance

TURN_SEPARATOR = "\n"


@dataclass(frozen=True, slots=True)
class IngestResult:
    source_id: "uuid.UUID"  # noqa: F821
    session_id: "uuid.UUID"  # noqa: F821
    utterance_ids: tuple["uuid.UUID", ...]  # noqa: F821
    transcript: str


def normalized_transcript(turns: Iterable[Turn]) -> str:
    return TURN_SEPARATOR.join(t.text for t in turns)


def ingest_text_transcript(
    db: OrmSession,
    *,
    subject_id: "uuid.UUID",  # noqa: F821
    session_no: int,
    turns: list[Turn],
    storage_uri: str,
    recorded_at: datetime | None = None,
) -> IngestResult:
    if not turns:
        raise ValueError("turns must not be empty")

    source = insert_source(db, subject_id=subject_id, kind="text", storage_uri=storage_uri)
    session = insert_session(
        db,
        subject_id=subject_id,
        source_id=source.id,
        session_no=session_no,
        recorded_at=recorded_at,
    )

    utterance_ids: list = []
    cursor = 0
    sep_len = len(TURN_SEPARATOR)
    for idx, turn in enumerate(turns):
        char_start = cursor
        char_end = char_start + len(turn.text)
        row = insert_utterance(
            db,
            session_id=session.id,
            speaker=turn.speaker,
            text=turn.text,
            char_start=char_start,
            char_end=char_end,
            ts_start_ms=turn.ts_start_ms,
            ts_end_ms=turn.ts_end_ms,
        )
        utterance_ids.append(row.id)
        cursor = char_end + (sep_len if idx < len(turns) - 1 else 0)

    return IngestResult(
        source_id=source.id,
        session_id=session.id,
        utterance_ids=tuple(utterance_ids),
        transcript=normalized_transcript(turns),
    )
