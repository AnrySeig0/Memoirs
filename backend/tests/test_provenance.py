"""§1 Provenance test (M6 acceptance gate).

> Lấy ngẫu nhiên 100 claim đã review → tỉ lệ truy vết đúng về nguồn = 100%.

This is the system-wide audit. It builds a realistic corpus that
exercises every milestone — M1 ingestion (utterances with offsets,
Vietnamese diacritics), M2 grounded extraction, M3 review actions
(accept/reject/edit/flag), M4 supersede chains, M5 merge chains —
then samples 100 reviewed claims with a seeded RNG and asserts every
single one passes `audit_provenance`.

If a sampled claim fails, the test prints all of its issues. This is
deliberately not short-circuiting per failure: when M6 fails we want
to know WHY every break happened, not just the first.
"""
import random

from sqlalchemy import select

from app.db.models import Claim
from app.core.audit import audit_provenance

from tests.fixtures.corpus import build_realistic_corpus


def test_100_random_reviewed_claims_trace_correctly(db_session) -> None:
    # Build a corpus large enough to leave headroom around 100.
    fixture = build_realistic_corpus(db_session, seed=42, sessions_per_subject=8)

    # Surface stats so a CI run shows what was audited even on success.
    total_claims = db_session.execute(
        select(Claim).where(Claim.subject_id.in_(fixture.subject_ids))
    ).scalars().all()
    counts: dict[str, int] = {}
    for c in total_claims:
        counts[c.status] = counts.get(c.status, 0) + 1
    print(f"\n[M6] corpus: {len(total_claims)} total claims, status breakdown: {counts}")

    assert len(fixture.reviewed_claim_ids) >= 100, (
        f"corpus produced only {len(fixture.reviewed_claim_ids)} reviewed "
        "claims; need ≥100 for the §1 sample. Bump sessions_per_subject "
        "or templates in tests/fixtures/corpus.py."
    )

    # Seeded random sample — reproducible across runs.
    rng = random.Random(20260602)
    sample_ids = rng.sample(fixture.reviewed_claim_ids, 100)

    failures: list[str] = []
    for cid in sample_ids:
        result = audit_provenance(db_session, claim_id=cid)
        if not result.ok:
            failures.append(result.summary)

    assert failures == [], (
        f"\n§1 M6 Provenance test FAILED on {len(failures)}/100 claims:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def test_every_reviewed_state_present_in_corpus(db_session) -> None:
    """Smoke check: the fixture genuinely exercises each milestone.

    If a refactor accidentally drops 'edited' or 'superseded' from the
    builder's review rotation, the 100-claim audit might pass while
    actually testing far less than §1 intends. This test fails noisily
    in that case.
    """
    fixture = build_realistic_corpus(db_session, seed=42, sessions_per_subject=8)

    statuses_present = set(
        db_session.execute(
            select(Claim.status).where(
                Claim.subject_id.in_(fixture.subject_ids)
            )
        ).scalars()
    )
    must_be_present = {"accepted", "rejected", "edited", "flagged", "superseded"}
    missing = must_be_present - statuses_present
    assert not missing, f"corpus missing reviewed states: {missing}"


def test_audit_holds_across_subjects(db_session) -> None:
    """Slightly different angle: instead of sampling, audit EVERY reviewed
    claim. With ~110-140 claims it's still fast (audit caches transcripts
    per session) and proves the §1 result isn't just luck of the sample.
    """
    fixture = build_realistic_corpus(db_session, seed=43, sessions_per_subject=6)

    failures: list[str] = []
    for cid in fixture.reviewed_claim_ids:
        result = audit_provenance(db_session, claim_id=cid)
        if not result.ok:
            failures.append(result.summary)
    assert failures == [], (
        f"\nFull-corpus audit FAILED on {len(failures)}/"
        f"{len(fixture.reviewed_claim_ids)} reviewed claims:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )
