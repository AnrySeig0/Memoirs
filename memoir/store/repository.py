"""Insert-only repository for the M1 substrate.

`utterances` exposes no update/delete by design — append-only is enforced
both here (no API) and at the DB layer (Postgres trigger).
"""
import uuid
from datetime import datetime

from sqlalchemy.orm import Session as OrmSession

from memoir.store.models import Session as SessionRow
from memoir.store.models import Source, Utterance


def insert_source(
    db: OrmSession,
    *,
    subject_id: uuid.UUID,
    kind: str,
    storage_uri: str,
) -> Source:
    if kind not in {"audio", "text"}:
        raise ValueError(f"kind must be 'audio' or 'text', got {kind!r}")
    row = Source(subject_id=subject_id, kind=kind, storage_uri=storage_uri)
    db.add(row)
    db.flush()
    return row


def insert_session(
    db: OrmSession,
    *,
    subject_id: uuid.UUID,
    source_id: uuid.UUID,
    session_no: int,
    recorded_at: datetime | None = None,
) -> SessionRow:
    row = SessionRow(
        subject_id=subject_id,
        source_id=source_id,
        session_no=session_no,
        recorded_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def insert_utterance(
    db: OrmSession,
    *,
    session_id: uuid.UUID,
    speaker: str,
    text: str,
    char_start: int,
    char_end: int,
    ts_start_ms: int | None = None,
    ts_end_ms: int | None = None,
) -> Utterance:
    if char_start < 0 or char_end < char_start:
        raise ValueError(
            f"invalid utterance offsets: char_start={char_start}, char_end={char_end}"
        )
    if char_end - char_start != len(text):
        raise ValueError(
            "utterance offset span does not match codepoint length of text "
            f"(end-start={char_end - char_start}, len(text)={len(text)})"
        )
    row = Utterance(
        session_id=session_id,
        speaker=speaker,
        text=text,
        char_start=char_start,
        char_end=char_end,
        ts_start_ms=ts_start_ms,
        ts_end_ms=ts_end_ms,
    )
    db.add(row)
    db.flush()
    return row
