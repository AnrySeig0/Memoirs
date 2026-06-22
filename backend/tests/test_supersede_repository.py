"""Repository-level tests for `supersede_claim` + `claim_history`.

The §1 Correction acceptance is in test_correction.py; this file
exercises the 7 invariants supersede must enforce, plus chain walking.
"""
import uuid

import pytest
from sqlalchemy import select, text as sa_text
from sqlalchemy.exc import IntegrityError

from app.services.ingest import Turn, ingest_text_transcript
from app.store import (
    Claim,
    ClaimNotFound,
    ReviewLog,
    claim_history,
    insert_claim_with_sources,
    supersede_claim,
)


def _pair(db_session, *, same_subject: bool = True) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (subject_id, claim_old_id, claim_new_id)."""
    subject_id = uuid.uuid4()
    s3 = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=1,
        turns=[Turn("subject", "Năm 1961, tôi chuyển nhà.")],
        storage_uri="s3://memoir/test/m4_old.txt",
    )
    c_old = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved in 1961.",
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(s3.utterance_ids),
        status="accepted",
    )
    new_subject_id = subject_id if same_subject else uuid.uuid4()
    s4 = ingest_text_transcript(
        db_session,
        subject_id=new_subject_id,
        session_no=1 if not same_subject else 2,
        turns=[Turn("subject", "Thực ra năm 1962.")],
        storage_uri="s3://memoir/test/m4_new.txt",
    )
    c_new = insert_claim_with_sources(
        db_session,
        subject_id=new_subject_id,
        text="Subject moved in 1962.",
        claim_type="event",
        confidence=0.85,
        source_utterance_ids=list(s4.utterance_ids),
        status="pending",
    )
    db_session.commit()
    return subject_id, c_old.id, c_new.id


def test_happy_path_writes_supersede_log(db_session) -> None:
    _, old_id, new_id = _pair(db_session)
    supersede_claim(
        db_session, old_id=old_id, new_id=new_id, actor="alice", note="self-correction"
    )
    db_session.commit()

    log = (
        db_session.execute(
            select(ReviewLog).where(ReviewLog.claim_id == old_id).order_by(ReviewLog.created_at)
        )
        .scalars()
        .all()
    )
    assert len(log) == 1
    assert log[0].action == "supersede"
    assert log[0].actor == "alice"
    assert log[0].payload["new_claim_id"] == str(new_id)
    assert log[0].payload["note"] == "self-correction"


def test_old_text_never_touched(db_session) -> None:
    """Hard rule from §6: 'KHÔNG đụng vào C_old.text'."""
    _, old_id, new_id = _pair(db_session)
    before = db_session.get(Claim, old_id).text
    supersede_claim(db_session, old_id=old_id, new_id=new_id, actor="alice")
    db_session.commit()
    after = db_session.get(Claim, old_id).text
    assert after == before


def test_self_supersede_refused(db_session) -> None:
    _, old_id, _ = _pair(db_session)
    with pytest.raises(ValueError, match="itself"):
        supersede_claim(db_session, old_id=old_id, new_id=old_id, actor="alice")


def test_cross_subject_supersede_refused(db_session) -> None:
    _, old_id, new_id = _pair(db_session, same_subject=False)
    with pytest.raises(ValueError, match="different subjects"):
        supersede_claim(db_session, old_id=old_id, new_id=new_id, actor="alice")


def test_supersede_already_superseded_refused(db_session) -> None:
    """Editors must supersede the leaf of a chain, not a historic node."""
    _, c1_id, c2_id = _pair(db_session)
    supersede_claim(db_session, old_id=c1_id, new_id=c2_id, actor="alice")
    db_session.commit()

    # Add a third claim. Now c1 is already superseded; attempting to
    # supersede it again should be refused.
    subject_id = db_session.get(Claim, c1_id).subject_id
    s5 = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=5,
        turns=[Turn("subject", "À, chính xác hơn là 1963.")],
        storage_uri="s3://memoir/test/m4_extra.txt",
    )
    c3 = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved in 1963.",
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(s5.utterance_ids),
    )
    db_session.commit()

    with pytest.raises(ValueError, match="already superseded"):
        supersede_claim(db_session, old_id=c1_id, new_id=c3.id, actor="alice")


def test_chain_supersede_via_leaf_works(db_session) -> None:
    """Chains are valid: C1 → C2 → C3 as long as each step supersedes the leaf."""
    _, c1_id, c2_id = _pair(db_session)
    supersede_claim(db_session, old_id=c1_id, new_id=c2_id, actor="alice")
    db_session.commit()

    subject_id = db_session.get(Claim, c1_id).subject_id
    s5 = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=5,
        turns=[Turn("subject", "1963 mới đúng.")],
        storage_uri="s3://memoir/test/m4_c3.txt",
    )
    c3 = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved in 1963.",
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(s5.utterance_ids),
    )
    db_session.commit()

    supersede_claim(db_session, old_id=c2_id, new_id=c3.id, actor="alice")
    db_session.commit()

    history = claim_history(db_session, claim_id=c2_id)
    assert [e.claim.id for e in history] == [c1_id, c2_id, c3.id]
    # Both non-leaf entries carry supersede metadata.
    assert history[0].superseded_at is not None
    assert history[1].superseded_at is not None
    assert history[2].superseded_at is None


def test_new_already_superseded_refused(db_session) -> None:
    _, c1_id, c2_id = _pair(db_session)
    supersede_claim(db_session, old_id=c1_id, new_id=c2_id, actor="alice")
    db_session.commit()
    # Now c2 itself is live (the leaf). Try to use c1 (already superseded)
    # as the *new* — should be refused.
    subject_id = db_session.get(Claim, c1_id).subject_id
    s5 = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=5,
        turns=[Turn("subject", "Câu khác.")],
        storage_uri="s3://memoir/test/m4_other.txt",
    )
    c_other = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="another claim",
        claim_type="event",
        confidence=0.5,
        source_utterance_ids=list(s5.utterance_ids),
    )
    db_session.commit()
    with pytest.raises(ValueError, match="itself superseded"):
        supersede_claim(db_session, old_id=c_other.id, new_id=c1_id, actor="alice")


def test_many_to_one_supersede_refused(db_session) -> None:
    """1:1 supersede. Many-to-one is merge (M5)."""
    subject_id, c1_id, c_new_id = _pair(db_session)
    # Create a second old claim under the same subject.
    s_extra = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=5,
        turns=[Turn("subject", "Câu khác về cùng sự kiện.")],
        storage_uri="s3://memoir/test/m4_extra2.txt",
    )
    c_other_old = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="another version",
        claim_type="event",
        confidence=0.6,
        source_utterance_ids=list(s_extra.utterance_ids),
    )
    db_session.commit()

    supersede_claim(db_session, old_id=c1_id, new_id=c_new_id, actor="alice")
    db_session.commit()

    with pytest.raises(ValueError, match="merge operation"):
        supersede_claim(
            db_session, old_id=c_other_old.id, new_id=c_new_id, actor="alice"
        )


def test_missing_old_or_new_raises_not_found(db_session) -> None:
    _, _, new_id = _pair(db_session)
    with pytest.raises(ClaimNotFound):
        supersede_claim(db_session, old_id=uuid.uuid4(), new_id=new_id, actor="alice")
    _, old_id, _ = _pair(db_session)
    with pytest.raises(ClaimNotFound):
        supersede_claim(db_session, old_id=old_id, new_id=uuid.uuid4(), actor="alice")


def test_empty_actor_refused(db_session) -> None:
    _, old_id, new_id = _pair(db_session)
    with pytest.raises(ValueError, match="actor"):
        supersede_claim(db_session, old_id=old_id, new_id=new_id, actor="")


def test_db_consistency_check_blocks_manual_drift(db_session) -> None:
    """Migration 0004 CHECK keeps (status='superseded') paired with superseded_by.

    If anyone tries to set superseded_by without status, or status without
    superseded_by, the DB refuses.
    """
    _, old_id, new_id = _pair(db_session)
    with pytest.raises(IntegrityError) as exc:
        db_session.execute(
            sa_text("UPDATE claims SET superseded_by = :n WHERE id = :o"),
            {"n": new_id, "o": old_id},
        )
        db_session.commit()
    assert "claims_supersede_consistency" in str(exc.value)
    db_session.rollback()

    with pytest.raises(IntegrityError) as exc:
        db_session.execute(
            sa_text("UPDATE claims SET status = 'superseded' WHERE id = :o"),
            {"o": old_id},
        )
        db_session.commit()
    assert "claims_supersede_consistency" in str(exc.value)
    db_session.rollback()
