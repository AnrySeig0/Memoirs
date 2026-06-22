"""Unit-ish tests for `audit_provenance`.

The big §1 acceptance test lives in test_provenance.py. This file
covers the audit function itself: happy paths on freshly-ingested
claims, recognition of each invariant break, and behavior on claim
states (pending / accepted / edited / superseded).

Most M1–M5 invariants are enforced at write time, so producing a
"broken" provenance state to negative-test against requires bypassing
the schema. For those cases we use raw SQL with DISABLE TRIGGER /
session-level superuser permissions. Where we can't reach a state
naturally we leave it to the schema-level test in the corresponding
milestone PR.
"""
import uuid

from sqlalchemy import text as sa_text

from app.services.ingest import Turn, ingest_text_transcript
from app.store import (
    accept_claim,
    audit_provenance,
    edit_claim,
    insert_claim_with_sources,
    merge_claim,
    set_claim_embedding,
    supersede_claim,
)
from app.services.resolve import DeterministicEmbedder


def _claim_in(db_session, subject_id, session_no, text):
    ingest = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=session_no,
        turns=[Turn("subject", text)],
        storage_uri=f"s3://memoir/test/audit_{session_no}.txt",
    )
    return insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text=text,
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(ingest.utterance_ids),
    )


def test_audit_passes_on_fresh_claim(db_session) -> None:
    subject_id = uuid.uuid4()
    claim = _claim_in(db_session, subject_id, 1, "Năm 1962 tôi chuyển đến Detroit.")
    db_session.commit()

    result = audit_provenance(db_session, claim_id=claim.id)
    assert result.ok, result.summary
    assert result.issues == []


def test_audit_passes_on_accepted_claim(db_session) -> None:
    subject_id = uuid.uuid4()
    claim = _claim_in(db_session, subject_id, 1, "Năm 1965.")
    db_session.commit()
    accept_claim(db_session, claim_id=claim.id, actor="alice")
    db_session.commit()
    assert audit_provenance(db_session, claim_id=claim.id).ok


def test_audit_passes_on_edited_claim_with_recoverable_history(db_session) -> None:
    subject_id = uuid.uuid4()
    claim = _claim_in(db_session, subject_id, 1, "Năm 1965.")
    db_session.commit()
    edit_claim(db_session, claim_id=claim.id, actor="alice", new_text="Năm 1965 (sửa).")
    db_session.commit()

    result = audit_provenance(db_session, claim_id=claim.id)
    assert result.ok, result.summary


def test_audit_passes_on_supersede_chain(db_session) -> None:
    subject_id = uuid.uuid4()
    old = _claim_in(db_session, subject_id, 1, "Năm 1961.")
    new = _claim_in(db_session, subject_id, 2, "Năm 1962.")
    db_session.commit()
    supersede_claim(db_session, old_id=old.id, new_id=new.id, actor="alice")
    db_session.commit()

    assert audit_provenance(db_session, claim_id=old.id).ok
    assert audit_provenance(db_session, claim_id=new.id).ok


def test_audit_passes_on_merge_chain(db_session) -> None:
    subject_id = uuid.uuid4()
    embedder = DeterministicEmbedder()
    a = _claim_in(db_session, subject_id, 1, "Detroit 1962.")
    b = _claim_in(db_session, subject_id, 2, "Subject moved to Detroit in 1962.")
    set_claim_embedding(db_session, claim_id=a.id, vector=embedder.embed(a.text))
    set_claim_embedding(db_session, claim_id=b.id, vector=embedder.embed(b.text))
    db_session.commit()
    merge_claim(db_session, loser_id=a.id, winner_id=b.id, actor="alice", similarity=0.9)
    db_session.commit()
    assert audit_provenance(db_session, claim_id=a.id).ok
    assert audit_provenance(db_session, claim_id=b.id).ok


def test_audit_flags_missing_claim(db_session) -> None:
    result = audit_provenance(db_session, claim_id=uuid.uuid4())
    assert not result.ok
    assert any("missing" in issue for issue in result.issues)


def test_audit_flags_offset_drift_via_raw_sql(db_session) -> None:
    """Manually break an offset via DISABLE TRIGGER on utterances and
    confirm the audit catches it. This is the worst-case data-quality
    scenario the audit exists to detect.
    """
    subject_id = uuid.uuid4()
    claim = _claim_in(db_session, subject_id, 1, "Năm 1962 ở Detroit.")
    db_session.commit()
    # Sanity check before damage:
    assert audit_provenance(db_session, claim_id=claim.id).ok

    # Bypass the append-only trigger as a session superuser to introduce
    # a 1-char offset shift. This is purely to exercise the audit; no
    # production code path can do this.
    db_session.execute(sa_text("ALTER TABLE utterances DISABLE TRIGGER USER"))
    db_session.execute(
        sa_text(
            "UPDATE utterances SET char_end = char_end - 1 "
            "WHERE id IN (SELECT utterance_id FROM claim_sources WHERE claim_id = :c)"
        ),
        {"c": claim.id},
    )
    db_session.execute(sa_text("ALTER TABLE utterances ENABLE TRIGGER USER"))
    db_session.commit()

    result = audit_provenance(db_session, claim_id=claim.id)
    assert not result.ok
    assert any("offset" in issue or "codepoint length" in issue for issue in result.issues)
