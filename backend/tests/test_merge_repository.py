"""Repository-level tests for `merge_claim`.

merge_claim is the relaxed-1:1 cousin of supersede_claim:
- same mechanical update (loser.status='superseded', superseded_by=winner)
- same text-untouched guarantee
- many losers may share a winner (relaxation vs M4)
- audit row carries action='merge' + similarity in payload
"""
import uuid

import pytest
from sqlalchemy import select

from app.services.ingest import Turn, ingest_text_transcript
from app.store import (
    Claim,
    ClaimNotFound,
    ReviewLog,
    insert_claim_with_sources,
    merge_claim,
)


def _claim_in(db_session, subject_id, session_no, text, *, status="pending") -> Claim:
    ingest = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=session_no,
        turns=[Turn("subject", text)],
        storage_uri=f"s3://memoir/test/m5_merge_{session_no}.txt",
    )
    return insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text=text,
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(ingest.utterance_ids),
        status=status,
    )


def test_happy_path_merges_loser_into_winner(db_session) -> None:
    subject_id = uuid.uuid4()
    loser = _claim_in(db_session, subject_id, 1, "Subject moved to Detroit in 1962.")
    winner = _claim_in(db_session, subject_id, 2, "Subject moved to Detroit in 1962.")
    db_session.commit()

    updated = merge_claim(
        db_session,
        loser_id=loser.id,
        winner_id=winner.id,
        actor="alice",
        similarity=0.98,
        note="duplicate phrasing",
    )
    db_session.commit()

    assert updated.id == loser.id
    assert updated.status == "superseded"
    assert updated.superseded_by == winner.id
    assert updated.text == "Subject moved to Detroit in 1962.", "loser text must be preserved"
    # winner untouched.
    reloaded_winner = db_session.get(Claim, winner.id)
    assert reloaded_winner.status == "pending"
    assert reloaded_winner.superseded_by is None

    log = (
        db_session.execute(
            select(ReviewLog).where(ReviewLog.claim_id == loser.id)
        )
        .scalars()
        .all()
    )
    assert len(log) == 1
    assert log[0].action == "merge"
    assert log[0].payload["winner_claim_id"] == str(winner.id)
    assert log[0].payload["similarity"] == pytest.approx(0.98)
    assert log[0].payload["note"] == "duplicate phrasing"


def test_many_to_one_merge_allowed(db_session) -> None:
    """The M5 relaxation: same winner can absorb multiple losers."""
    subject_id = uuid.uuid4()
    l1 = _claim_in(db_session, subject_id, 1, "Detroit 1962.")
    l2 = _claim_in(db_session, subject_id, 2, "Detroit, year '62.")
    winner = _claim_in(db_session, subject_id, 3, "Subject moved to Detroit in 1962.")
    db_session.commit()

    merge_claim(db_session, loser_id=l1.id, winner_id=winner.id, actor="alice")
    merge_claim(db_session, loser_id=l2.id, winner_id=winner.id, actor="alice")
    db_session.commit()

    # Both losers point at the same winner.
    assert db_session.get(Claim, l1.id).superseded_by == winner.id
    assert db_session.get(Claim, l2.id).superseded_by == winner.id


def test_self_merge_refused(db_session) -> None:
    subject_id = uuid.uuid4()
    c = _claim_in(db_session, subject_id, 1, "x")
    db_session.commit()
    with pytest.raises(ValueError, match="itself"):
        merge_claim(db_session, loser_id=c.id, winner_id=c.id, actor="alice")


def test_cross_subject_merge_refused(db_session) -> None:
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    loser = _claim_in(db_session, s1, 1, "x")
    winner = _claim_in(db_session, s2, 1, "x")
    db_session.commit()
    with pytest.raises(ValueError, match="different subjects"):
        merge_claim(db_session, loser_id=loser.id, winner_id=winner.id, actor="alice")


def test_merging_superseded_loser_refused(db_session) -> None:
    subject_id = uuid.uuid4()
    a = _claim_in(db_session, subject_id, 1, "a")
    b = _claim_in(db_session, subject_id, 2, "b")
    c = _claim_in(db_session, subject_id, 3, "c")
    db_session.commit()
    merge_claim(db_session, loser_id=a.id, winner_id=b.id, actor="alice")
    db_session.commit()
    with pytest.raises(ValueError, match="already superseded"):
        merge_claim(db_session, loser_id=a.id, winner_id=c.id, actor="alice")


def test_merging_into_superseded_winner_refused(db_session) -> None:
    subject_id = uuid.uuid4()
    a = _claim_in(db_session, subject_id, 1, "a")
    b = _claim_in(db_session, subject_id, 2, "b")
    c = _claim_in(db_session, subject_id, 3, "c")
    db_session.commit()
    merge_claim(db_session, loser_id=a.id, winner_id=b.id, actor="alice")
    db_session.commit()
    # `a` is now superseded; using it as a winner is a dead target.
    with pytest.raises(ValueError, match="itself superseded"):
        merge_claim(db_session, loser_id=c.id, winner_id=a.id, actor="alice")


def test_missing_id_raises_not_found(db_session) -> None:
    subject_id = uuid.uuid4()
    c = _claim_in(db_session, subject_id, 1, "x")
    db_session.commit()
    with pytest.raises(ClaimNotFound):
        merge_claim(db_session, loser_id=uuid.uuid4(), winner_id=c.id, actor="alice")
    with pytest.raises(ClaimNotFound):
        merge_claim(db_session, loser_id=c.id, winner_id=uuid.uuid4(), actor="alice")


def test_empty_actor_refused(db_session) -> None:
    subject_id = uuid.uuid4()
    a = _claim_in(db_session, subject_id, 1, "a")
    b = _claim_in(db_session, subject_id, 2, "b")
    db_session.commit()
    with pytest.raises(ValueError, match="actor"):
        merge_claim(db_session, loser_id=a.id, winner_id=b.id, actor="   ")


def test_similarity_out_of_range_refused(db_session) -> None:
    subject_id = uuid.uuid4()
    a = _claim_in(db_session, subject_id, 1, "a")
    b = _claim_in(db_session, subject_id, 2, "b")
    db_session.commit()
    with pytest.raises(ValueError, match="similarity"):
        merge_claim(db_session, loser_id=a.id, winner_id=b.id, actor="alice", similarity=1.5)


def test_supersede_path_still_strict_after_m5(db_session) -> None:
    """Sanity: M4 supersede keeps its 1:1 invariant — merge is the
    deliberately-relaxed path, not a backdoor weakening of supersede.
    """
    from app.store import supersede_claim

    subject_id = uuid.uuid4()
    old1 = _claim_in(db_session, subject_id, 1, "x1")
    old2 = _claim_in(db_session, subject_id, 2, "x2")
    shared_new = _claim_in(db_session, subject_id, 3, "new")
    db_session.commit()

    supersede_claim(db_session, old_id=old1.id, new_id=shared_new.id, actor="alice")
    db_session.commit()
    with pytest.raises(ValueError, match="merge operation"):
        supersede_claim(db_session, old_id=old2.id, new_id=shared_new.id, actor="alice")
