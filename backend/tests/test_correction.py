"""§1 Correction test (M4 acceptance gate).

> Tạo một correction → claim cũ vẫn tồn tại, được đánh dấu superseded,
> truy được "đã nói gì → sửa thành gì → khi nào".

The fixture mirrors the §6 vignette exactly: subject says one thing in
session 3, corrects it in session 4. The hard assertion is that the
old claim's TEXT does not move — drift becomes visible, not silent.
"""
import uuid

from app.services.ingest import Turn, ingest_text_transcript
from app.db.models import Claim
from app.repositories.claim import ClaimNotFound, claim_history, insert_claim_with_sources, supersede_claim


def test_correction_preserves_old_and_traces_history(db_session) -> None:
    subject_id = uuid.uuid4()

    # Session 3: subject says "1961"
    s3 = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=3,
        turns=[Turn("subject", "Tôi chuyển đến Detroit năm 1961.")],
        storage_uri="s3://memoir/test/m4_s3.txt",
    )
    c_old = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved to Detroit in 1961.",
        claim_type="event",
        confidence=0.8,
        source_utterance_ids=list(s3.utterance_ids),
        status="accepted",
    )
    db_session.commit()

    # Session 4: subject corrects to "1962"
    s4 = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=4,
        turns=[
            Turn("subject", "Thực ra là năm 1962, không phải 1961."),
        ],
        storage_uri="s3://memoir/test/m4_s4.txt",
    )
    c_new = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved to Detroit in 1962.",
        claim_type="event",
        confidence=0.9,
        source_utterance_ids=list(s4.utterance_ids),
        status="pending",
    )
    db_session.commit()

    # The editor confirms the correction.
    supersede_claim(
        db_session,
        old_id=c_old.id,
        new_id=c_new.id,
        actor="alice",
        note="confirmed self-correction in session 4",
    )
    db_session.commit()

    # --- §1 hard assertions ---

    # 1. The old claim STILL EXISTS — supersede never deletes.
    reloaded_old = db_session.get(Claim, c_old.id)
    assert reloaded_old is not None

    # 2. The old claim's TEXT is unchanged. "1961" is preserved exactly
    #    as it was — drift visible, not silent.
    assert reloaded_old.text == "Subject moved to Detroit in 1961."

    # 3. The old claim is marked superseded and points at the new one.
    assert reloaded_old.status == "superseded"
    assert reloaded_old.superseded_by == c_new.id

    # 4. The new claim is untouched by the supersede operation itself.
    reloaded_new = db_session.get(Claim, c_new.id)
    assert reloaded_new.text == "Subject moved to Detroit in 1962."
    assert reloaded_new.status == "pending"
    assert reloaded_new.superseded_by is None

    # --- §6 "lịch sử một sự kiện" — đã nói gì → sửa thành gì → khi nào ---

    history = claim_history(db_session, claim_id=c_old.id)
    assert len(history) == 2

    # đã nói gì (root):
    root = history[0]
    assert root.claim.id == c_old.id
    assert root.claim.text == "Subject moved to Detroit in 1961."
    # khi nào (the supersede moment is recorded on the root entry):
    assert root.superseded_at is not None
    assert root.superseded_by_actor == "alice"
    assert root.note == "confirmed self-correction in session 4"

    # sửa thành gì (leaf):
    leaf = history[1]
    assert leaf.claim.id == c_new.id
    assert leaf.claim.text == "Subject moved to Detroit in 1962."
    # leaf hasn't been superseded — its superseded_* fields are null.
    assert leaf.superseded_at is None
    assert leaf.superseded_by_actor is None

    # Same chain regardless of which claim we ask from.
    history_from_leaf = claim_history(db_session, claim_id=c_new.id)
    assert [e.claim.id for e in history_from_leaf] == [c_old.id, c_new.id]


def test_history_for_unknown_claim_raises(db_session) -> None:
    import pytest

    with pytest.raises(ClaimNotFound):
        claim_history(db_session, claim_id=uuid.uuid4())
