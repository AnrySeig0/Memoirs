"""Repository-level enforcement of the M2 grounding rule.

The Pydantic schema catches ungrounded extractions before they reach the
repo. This test catches anyone calling `insert_claim_with_sources`
directly — e.g. future review/edit flows that bypass the Pydantic layer
— and trying to write a claim with zero sources.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.services.ingest import Turn, ingest_text_transcript
from app.store import Claim, ClaimSource, insert_claim_with_sources


def test_empty_source_list_rejected_before_db(db_session) -> None:
    """No claim row is written when source list is empty."""
    with pytest.raises(ValueError, match="at least one source"):
        insert_claim_with_sources(
            db_session,
            subject_id=uuid.uuid4(),
            text="ungrounded claim",
            claim_type="fact",
            confidence=0.5,
            source_utterance_ids=[],
        )
    # The DB must remain empty — we should NOT have written a claim row
    # then later realized there are no sources.
    rows = db_session.execute(select(Claim)).scalars().all()
    assert rows == []


def test_invalid_confidence_rejected(db_session) -> None:
    with pytest.raises(ValueError, match="confidence"):
        insert_claim_with_sources(
            db_session,
            subject_id=uuid.uuid4(),
            text="x",
            claim_type="fact",
            confidence=1.5,
            source_utterance_ids=[uuid.uuid4()],
        )


def test_invalid_status_rejected(db_session) -> None:
    with pytest.raises(ValueError, match="status"):
        insert_claim_with_sources(
            db_session,
            subject_id=uuid.uuid4(),
            text="x",
            claim_type="fact",
            confidence=0.5,
            source_utterance_ids=[uuid.uuid4()],
            status="bogus",
        )


def test_claim_and_sources_written_atomically(db_session) -> None:
    subject_id = uuid.uuid4()
    result = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=1,
        turns=[Turn("subject", "Năm 1962 tôi chuyển đến Detroit.")],
        storage_uri="s3://memoir/test/m2_repo.txt",
    )
    db_session.commit()

    claim = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved to Detroit in 1962.",
        claim_type="event",
        confidence=0.8,
        source_utterance_ids=list(result.utterance_ids),
    )
    db_session.commit()

    sources = (
        db_session.execute(
            select(ClaimSource).where(ClaimSource.claim_id == claim.id)
        )
        .scalars()
        .all()
    )
    assert {s.utterance_id for s in sources} == set(result.utterance_ids)


def test_duplicate_source_ids_deduped(db_session) -> None:
    """Caller hands in the same utterance twice — repo dedups instead of
    crashing on the composite PK. Same grounding, one row.
    """
    subject_id = uuid.uuid4()
    result = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=2,
        turns=[Turn("subject", "Năm 1970.")],
        storage_uri="s3://memoir/test/m2_dedup.txt",
    )
    db_session.commit()

    only_utt = result.utterance_ids[0]
    claim = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Something happened in 1970.",
        claim_type="event",
        confidence=0.6,
        source_utterance_ids=[only_utt, only_utt, only_utt],
    )
    db_session.commit()

    sources = (
        db_session.execute(
            select(ClaimSource).where(ClaimSource.claim_id == claim.id)
        )
        .scalars()
        .all()
    )
    assert len(sources) == 1


def test_claim_sources_append_only_at_db(db_session) -> None:
    """Belt-and-braces: even if app code somehow tries to rewrite a
    grounding row, the DB trigger refuses it (mirrors utterances trigger).
    """
    from sqlalchemy import text as sa_text

    subject_id = uuid.uuid4()
    result = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=3,
        turns=[Turn("subject", "Năm 1980.")],
        storage_uri="s3://memoir/test/m2_appendonly.txt",
    )
    db_session.commit()
    claim = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="x",
        claim_type="event",
        confidence=0.5,
        source_utterance_ids=list(result.utterance_ids),
    )
    db_session.commit()

    with pytest.raises(IntegrityError) as exc:
        db_session.execute(
            sa_text("DELETE FROM claim_sources WHERE claim_id = :c"),
            {"c": claim.id},
        )
        db_session.commit()
    assert "append-only" in str(exc.value)
    db_session.rollback()
