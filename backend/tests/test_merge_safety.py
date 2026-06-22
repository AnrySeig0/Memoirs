"""§1 Merge safety test (M5 acceptance gate).

> Không có merge tự động nào được commit mà không qua xác nhận của người.

The test takes the strongest possible reading: dedup-candidate discovery
is purely a SELECT — it MUST NOT cause a single row change anywhere in
the DB. The only path that mutates claims/review_log is an explicit
human-driven call to `merge_claim` (the repo function) or
`POST /claims/{loser}/merge` (the API).

Setup: build a fixture where automation, if it were free to "fix
duplicates" on its own, would have plenty to merge — five near-
identical claims. Take a global before/after snapshot of every row in
every table that merge could touch. Run find_merge_candidates. Confirm
nothing moved.

Then, separately, exercise the human path and confirm rows DO change
exactly once per explicit confirmation.
"""
import uuid

from sqlalchemy import func, select

from app.ingest import Turn, ingest_text_transcript
from app.resolve import DeterministicEmbedder, find_merge_candidates
from app.store import (
    Claim,
    ReviewLog,
    insert_claim_with_sources,
    merge_claim,
    set_claim_embedding,
)


def _snapshot(db_session) -> dict[str, int]:
    """Aggregate row counts and audit-log-by-action counts.

    Anything an auto-merge would do MUST move at least one of these
    numbers — counting status='superseded' claims, counting review_log
    rows by action.
    """
    counts: dict[str, int] = {}
    counts["claims_total"] = db_session.execute(
        select(func.count()).select_from(Claim)
    ).scalar_one()
    for status_value in ("pending", "accepted", "rejected", "edited", "flagged", "superseded"):
        counts[f"claims_{status_value}"] = db_session.execute(
            select(func.count()).select_from(Claim).where(Claim.status == status_value)
        ).scalar_one()
    counts["review_log_total"] = db_session.execute(
        select(func.count()).select_from(ReviewLog)
    ).scalar_one()
    for action in ("accept", "reject", "edit", "flag", "merge", "supersede"):
        counts[f"review_log_{action}"] = db_session.execute(
            select(func.count()).select_from(ReviewLog).where(ReviewLog.action == action)
        ).scalar_one()
    return counts


def _seed_duplicates(db_session, subject_id: uuid.UUID, count: int) -> list[uuid.UUID]:
    embedder = DeterministicEmbedder()
    text = "Subject moved to Detroit in 1962."
    ids: list[uuid.UUID] = []
    for i in range(count):
        ingest = ingest_text_transcript(
            db_session,
            subject_id=subject_id,
            session_no=i + 1,
            turns=[Turn("subject", text)],
            storage_uri=f"s3://memoir/test/m5_safety_{i}.txt",
        )
        claim = insert_claim_with_sources(
            db_session,
            subject_id=subject_id,
            text=text,
            claim_type="event",
            confidence=0.7,
            source_utterance_ids=list(ingest.utterance_ids),
        )
        set_claim_embedding(db_session, claim_id=claim.id, vector=embedder.embed(text))
        ids.append(claim.id)
    db_session.commit()
    return ids


def test_find_merge_candidates_writes_nothing(db_session) -> None:
    """The §1 hard rule: candidate discovery does not commit a merge."""
    subject_id = uuid.uuid4()
    ids = _seed_duplicates(db_session, subject_id, count=5)
    db_session.commit()

    before = _snapshot(db_session)

    # 5 claims → C(5,2) = 10 possible pairs, all identical text, all
    # above any sensible threshold. Maximum temptation for an auto-merge.
    pairs = find_merge_candidates(db_session, subject_id=subject_id, threshold=0.5)
    assert len(pairs) == 10, "fixture should produce all 10 duplicate pairs"

    after = _snapshot(db_session)
    assert before == after, (
        "find_merge_candidates committed something — that's the bug §1 "
        f"safety test exists to catch. delta: "
        f"{ {k: (before[k], after[k]) for k in before if before[k] != after[k]} }"
    )

    # And the actual claim rows are untouched: status, superseded_by,
    # reviewed_at, reviewed_by all unchanged on every fixture claim.
    for cid in ids:
        c = db_session.get(Claim, cid)
        assert c.status == "pending"
        assert c.superseded_by is None
        assert c.reviewed_at is None
        assert c.reviewed_by is None


def test_only_explicit_human_merge_commits(db_session) -> None:
    """The complementary half: one explicit merge_claim call → exactly one
    superseded row + exactly one 'merge' audit row. Nothing more.
    """
    subject_id = uuid.uuid4()
    ids = _seed_duplicates(db_session, subject_id, count=5)
    db_session.commit()

    # Snapshot before the single human action.
    before = _snapshot(db_session)

    merge_claim(
        db_session,
        loser_id=ids[0],
        winner_id=ids[1],
        actor="alice",
        similarity=1.0,
    )
    db_session.commit()

    after = _snapshot(db_session)

    # Exactly one claim flipped to superseded.
    assert after["claims_superseded"] - before["claims_superseded"] == 1
    assert after["claims_pending"] - before["claims_pending"] == -1
    # Exactly one 'merge' audit row.
    assert after["review_log_merge"] - before["review_log_merge"] == 1
    # No other audit kind moved.
    for action in ("accept", "reject", "edit", "flag", "supersede"):
        assert after[f"review_log_{action}"] == before[f"review_log_{action}"]

    # The 3 untouched duplicates are still pending — automation did not
    # opportunistically clean them up.
    for cid in ids[2:]:
        c = db_session.get(Claim, cid)
        assert c.status == "pending"
        assert c.superseded_by is None


def test_api_dedup_candidates_is_idempotent_read(api_client, db_session) -> None:
    """The HTTP surface: calling GET /claims/dedup-candidates twice produces
    identical results, and zero row changes between calls.
    """
    subject_id = uuid.uuid4()
    _seed_duplicates(db_session, subject_id, count=4)
    db_session.commit()

    before = _snapshot(db_session)
    r1 = api_client.get(
        "/claims/dedup-candidates",
        params={"subject_id": str(subject_id), "threshold": 0.5},
    )
    mid = _snapshot(db_session)
    r2 = api_client.get(
        "/claims/dedup-candidates",
        params={"subject_id": str(subject_id), "threshold": 0.5},
    )
    after = _snapshot(db_session)

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()
    assert before == mid == after
