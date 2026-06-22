"""DB tests for `find_merge_candidates`.

Read-only on purpose: every test sets up claims with deterministic
embeddings, calls find_merge_candidates, and asserts both (a) the right
pairs surface and (b) nothing was written. The dedicated §1 Merge
safety acceptance test lives in test_merge_safety.py.
"""
import uuid

import pytest

from app.ingest import Turn, ingest_text_transcript
from app.resolve import DeterministicEmbedder, find_merge_candidates
from app.store import (
    insert_claim_with_sources,
    set_claim_embedding,
)


def _claim(db_session, subject_id, text, *, status="pending", session_no=None) -> uuid.UUID:
    """Create a session+claim, embed it deterministically by its text,
    return the claim id.
    """
    session_no = session_no if session_no is not None else _claim._counter[0]
    _claim._counter[0] += 1
    ingest = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=session_no,
        turns=[Turn("subject", text)],
        storage_uri=f"s3://memoir/test/m5_dedup_{session_no}.txt",
    )
    claim = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text=text,
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(ingest.utterance_ids),
        status=status,
    )
    vec = DeterministicEmbedder().embed(text)
    set_claim_embedding(db_session, claim_id=claim.id, vector=vec)
    return claim.id


_claim._counter = [1]  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _reset_counter():
    _claim._counter[0] = 1
    yield


def test_identical_texts_surface_as_candidate(db_session) -> None:
    subject_id = uuid.uuid4()
    a = _claim(db_session, subject_id, "Subject moved to Detroit in 1962.")
    b = _claim(db_session, subject_id, "Subject moved to Detroit in 1962.")
    _claim(db_session, subject_id, "Subject's father worked on the railway.")
    db_session.commit()

    pairs = find_merge_candidates(db_session, subject_id=subject_id, threshold=0.9)
    pair_ids = {tuple(sorted([p.claim_a_id, p.claim_b_id])) for p in pairs}
    assert tuple(sorted([a, b])) in pair_ids
    assert all(p.similarity >= 0.9 for p in pairs)


def test_unrelated_texts_do_not_surface(db_session) -> None:
    subject_id = uuid.uuid4()
    _claim(db_session, subject_id, "Subject moved to Detroit in 1962.")
    _claim(db_session, subject_id, "Subject's father worked on the railway.")
    _claim(db_session, subject_id, "Subject married in 1985 in Hanoi.")
    db_session.commit()

    pairs = find_merge_candidates(db_session, subject_id=subject_id, threshold=0.5)
    # DeterministicEmbedder gives near-orthogonal vectors for distinct
    # texts; nothing should land above 0.5.
    assert pairs == []


def test_superseded_claims_excluded(db_session) -> None:
    """A merged-away claim must not reappear as a candidate."""
    from app.store import merge_claim

    subject_id = uuid.uuid4()
    a = _claim(db_session, subject_id, "Subject moved to Detroit in 1962.")
    b = _claim(db_session, subject_id, "Subject moved to Detroit in 1962.")
    db_session.commit()
    merge_claim(db_session, loser_id=a, winner_id=b, actor="alice", similarity=1.0)
    db_session.commit()

    pairs = find_merge_candidates(db_session, subject_id=subject_id, threshold=0.5)
    assert pairs == [], "superseded claims must drop out of dedup queue"


def test_unembedded_claims_excluded(db_session) -> None:
    subject_id = uuid.uuid4()
    ingest = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=99,  # avoid clashing with _claim's autoincrementing counter
        turns=[Turn("subject", "Năm 1962.")],
        storage_uri="s3://memoir/test/m5_unembedded.txt",
    )
    unembedded = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved to Detroit in 1962.",
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(ingest.utterance_ids),
    )
    # ← deliberately no set_claim_embedding here
    _claim(db_session, subject_id, "Subject moved to Detroit in 1962.")
    db_session.commit()

    pairs = find_merge_candidates(db_session, subject_id=subject_id, threshold=0.9)
    assert all(unembedded.id not in (p.claim_a_id, p.claim_b_id) for p in pairs)


def test_results_are_per_subject(db_session) -> None:
    s1 = uuid.uuid4()
    s2 = uuid.uuid4()
    _claim(db_session, s1, "Subject moved to Detroit in 1962.")
    _claim(db_session, s1, "Subject moved to Detroit in 1962.")
    _claim(db_session, s2, "Different person, same sentence.")
    _claim(db_session, s2, "Different person, same sentence.")
    db_session.commit()

    s1_pairs = find_merge_candidates(db_session, subject_id=s1, threshold=0.9)
    s2_pairs = find_merge_candidates(db_session, subject_id=s2, threshold=0.9)
    assert len(s1_pairs) == 1
    assert len(s2_pairs) == 1
    # No cross-subject leakage — ids in each list belong to that subject only.
    from app.store import Claim

    for pair in s1_pairs:
        assert db_session.get(Claim, pair.claim_a_id).subject_id == s1
        assert db_session.get(Claim, pair.claim_b_id).subject_id == s1


def test_threshold_validation(db_session) -> None:
    with pytest.raises(ValueError):
        find_merge_candidates(db_session, subject_id=uuid.uuid4(), threshold=1.5)
    with pytest.raises(ValueError):
        find_merge_candidates(db_session, subject_id=uuid.uuid4(), threshold=-2.0)
