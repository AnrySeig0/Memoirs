"""Repository-level tests for M3 review actions.

Each accept/reject/edit/flag function must:
- mutate the claim (status, reviewed_at, reviewed_by)
- write exactly one review_log row in the same transaction
- carry the right action label + payload for that action

Edge cases: missing claim → ClaimNotFound; edit on superseded → refused;
the audit table is append-only at the DB layer (mirrors utterances /
claim_sources triggers).
"""
import uuid

import pytest
from sqlalchemy import select, text as sa_text
from sqlalchemy.exc import IntegrityError

from app.ingest import Turn, ingest_text_transcript
from app.store import (
    Claim,
    ClaimNotFound,
    ReviewLog,
    accept_claim,
    edit_claim,
    flag_claim,
    insert_claim_with_sources,
    reject_claim,
)


def _seed_claim(db_session, *, status: str = "pending") -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (subject_id, claim_id)."""
    subject_id = uuid.uuid4()
    ingest = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=1,
        turns=[Turn("subject", "Năm 1962 tôi chuyển đến Detroit.")],
        storage_uri="s3://memoir/test/m3_review.txt",
    )
    claim = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved to Detroit in 1962.",
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(ingest.utterance_ids),
        status=status,
    )
    db_session.commit()
    return subject_id, claim.id


def _logs_for(db_session, claim_id):
    return (
        db_session.execute(
            select(ReviewLog).where(ReviewLog.claim_id == claim_id).order_by(ReviewLog.created_at)
        )
        .scalars()
        .all()
    )


def test_accept_sets_status_and_writes_log(db_session) -> None:
    _, claim_id = _seed_claim(db_session)
    updated = accept_claim(db_session, claim_id=claim_id, actor="alice")
    db_session.commit()

    assert updated.status == "accepted"
    assert updated.reviewed_by == "alice"
    assert updated.reviewed_at is not None

    logs = _logs_for(db_session, claim_id)
    assert len(logs) == 1
    assert logs[0].action == "accept"
    assert logs[0].actor == "alice"


def test_reject_records_reason_in_payload(db_session) -> None:
    _, claim_id = _seed_claim(db_session)
    reject_claim(
        db_session,
        claim_id=claim_id,
        actor="alice",
        reason="duplicate of a more specific claim",
    )
    db_session.commit()

    logs = _logs_for(db_session, claim_id)
    assert len(logs) == 1
    assert logs[0].action == "reject"
    assert logs[0].payload == {"reason": "duplicate of a more specific claim"}


def test_edit_captures_previous_text(db_session) -> None:
    _, claim_id = _seed_claim(db_session)
    edited = edit_claim(
        db_session,
        claim_id=claim_id,
        actor="alice",
        new_text="Subject moved to Detroit, Michigan in 1962.",
    )
    db_session.commit()

    assert edited.status == "edited"
    assert edited.text == "Subject moved to Detroit, Michigan in 1962."

    logs = _logs_for(db_session, claim_id)
    assert len(logs) == 1
    assert logs[0].action == "edit"
    assert logs[0].payload["previous_text"] == "Subject moved to Detroit in 1962."
    assert logs[0].payload["new_text"] == "Subject moved to Detroit, Michigan in 1962."


def test_flag_with_reason(db_session) -> None:
    _, claim_id = _seed_claim(db_session)
    flag_claim(
        db_session,
        claim_id=claim_id,
        actor="alice",
        reason="possibly contradicts session 2 utterance 4",
    )
    db_session.commit()

    logs = _logs_for(db_session, claim_id)
    assert len(logs) == 1 and logs[0].action == "flag"
    assert "contradicts" in logs[0].payload["reason"]


def test_reversible_review_grows_log(db_session) -> None:
    """§1 'đảo ngược được' — accept then reject is a legal path, the log
    captures both rows, the most recent reviewed_by reflects the latter.
    """
    _, claim_id = _seed_claim(db_session)
    accept_claim(db_session, claim_id=claim_id, actor="alice")
    reject_claim(db_session, claim_id=claim_id, actor="bob", reason="re-checked")
    db_session.commit()

    logs = _logs_for(db_session, claim_id)
    assert [log.action for log in logs] == ["accept", "reject"]
    claim = db_session.get(Claim, claim_id)
    assert claim.status == "rejected"
    assert claim.reviewed_by == "bob"


def test_missing_claim_raises_not_found(db_session) -> None:
    with pytest.raises(ClaimNotFound):
        accept_claim(db_session, claim_id=uuid.uuid4(), actor="alice")


def test_edit_on_superseded_refused(db_session) -> None:
    """A superseded claim must reach that state via a real supersede chain
    (M4 invariant CHECK forbids the shortcut of inserting with
    status='superseded' but superseded_by NULL). Set up the chain then
    confirm `edit` on the historic claim is refused.
    """
    from app.store import supersede_claim

    subject_id, old_id = _seed_claim(db_session)
    # A second claim to serve as the successor.
    ingest = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=2,
        turns=[Turn("subject", "Thực ra năm 1963.")],
        storage_uri="s3://memoir/test/m3_edit_super_new.txt",
    )
    new_claim = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved to Detroit in 1963.",
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(ingest.utterance_ids),
    )
    db_session.commit()
    supersede_claim(db_session, old_id=old_id, new_id=new_claim.id, actor="alice")
    db_session.commit()

    with pytest.raises(ValueError, match="superseded"):
        edit_claim(
            db_session,
            claim_id=old_id,
            actor="alice",
            new_text="trying to mutate history",
        )
    # The supersede audit row is there, but the refused edit added nothing.
    logs = _logs_for(db_session, old_id)
    assert [log.action for log in logs] == ["supersede"]


def test_empty_actor_refused(db_session) -> None:
    _, claim_id = _seed_claim(db_session)
    with pytest.raises(ValueError, match="actor"):
        accept_claim(db_session, claim_id=claim_id, actor="   ")


def test_review_log_append_only_at_db(db_session) -> None:
    _, claim_id = _seed_claim(db_session)
    accept_claim(db_session, claim_id=claim_id, actor="alice")
    db_session.commit()

    log_id = _logs_for(db_session, claim_id)[0].id
    with pytest.raises(IntegrityError) as exc:
        db_session.execute(
            sa_text("UPDATE review_log SET actor = :a WHERE id = :i"),
            {"a": "mallory", "i": log_id},
        )
        db_session.commit()
    assert "append-only" in str(exc.value)
    db_session.rollback()

    with pytest.raises(IntegrityError) as exc:
        db_session.execute(sa_text("DELETE FROM review_log WHERE id = :i"), {"i": log_id})
        db_session.commit()
    assert "append-only" in str(exc.value)
    db_session.rollback()
