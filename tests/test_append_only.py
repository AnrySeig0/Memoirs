"""Append-only enforcement for utterances (M1 hard rule).

Verifies:
1. End-to-end ingestion writes rows that can be sliced back out of the
   normalized transcript by their stored char_start/char_end.
2. The Postgres trigger rejects UPDATE on utterances.
3. The Postgres trigger rejects DELETE on utterances.
"""
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from memoir.ingest import Turn, ingest_text_transcript
from memoir.store import Utterance


def test_text_ingest_roundtrip(db_session) -> None:
    turns = [
        Turn("subject", "Tôi sinh năm 1962."),
        Turn("interviewer", "Ở đâu ạ?"),
        Turn("subject", "Detroit, Michigan."),
    ]
    result = ingest_text_transcript(
        db_session,
        subject_id=uuid.uuid4(),
        session_no=1,
        turns=turns,
        storage_uri="s3://memoir/test/session1.txt",
    )
    db_session.commit()

    rows = (
        db_session.execute(
            select(Utterance)
            .where(Utterance.session_id == result.session_id)
            .order_by(Utterance.char_start)
        )
        .scalars()
        .all()
    )
    assert [r.text for r in rows] == [t.text for t in turns]
    assert [r.speaker for r in rows] == [t.speaker for t in turns]
    for row in rows:
        assert result.transcript[row.char_start : row.char_end] == row.text


def test_utterance_update_rejected(db_session) -> None:
    result = ingest_text_transcript(
        db_session,
        subject_id=uuid.uuid4(),
        session_no=2,
        turns=[Turn("subject", "Câu gốc.")],
        storage_uri="s3://memoir/test/session2.txt",
    )
    db_session.commit()

    target_id = result.utterance_ids[0]
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(
            text("UPDATE utterances SET text = :t WHERE id = :i"),
            {"t": "Câu đã sửa", "i": target_id},
        )
        db_session.commit()
    assert "append-only" in str(exc_info.value)
    db_session.rollback()

    still = db_session.get(Utterance, target_id)
    assert still is not None and still.text == "Câu gốc."


def test_utterance_delete_rejected(db_session) -> None:
    result = ingest_text_transcript(
        db_session,
        subject_id=uuid.uuid4(),
        session_no=3,
        turns=[Turn("subject", "Đừng xóa tôi.")],
        storage_uri="s3://memoir/test/session3.txt",
    )
    db_session.commit()

    target_id = result.utterance_ids[0]
    with pytest.raises(IntegrityError) as exc_info:
        db_session.execute(text("DELETE FROM utterances WHERE id = :i"), {"i": target_id})
        db_session.commit()
    assert "append-only" in str(exc_info.value)
    db_session.rollback()

    assert db_session.get(Utterance, target_id) is not None
