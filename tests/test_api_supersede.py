"""HTTP-level tests for the M4 supersede endpoints."""
import uuid

from memoir.ingest import Turn, ingest_text_transcript
from memoir.store import insert_claim_with_sources


def _pair(db_session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (subject_id, old_claim_id, new_claim_id)."""
    subject_id = uuid.uuid4()
    s_old = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=1,
        turns=[Turn("subject", "Năm 1961, tôi chuyển nhà.")],
        storage_uri="s3://memoir/test/m4_api_old.txt",
    )
    c_old = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved in 1961.",
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(s_old.utterance_ids),
        status="accepted",
    )
    s_new = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=2,
        turns=[Turn("subject", "Thực ra năm 1962.")],
        storage_uri="s3://memoir/test/m4_api_new.txt",
    )
    c_new = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved in 1962.",
        claim_type="event",
        confidence=0.9,
        source_utterance_ids=list(s_new.utterance_ids),
    )
    db_session.commit()
    return subject_id, c_old.id, c_new.id


def test_supersede_happy_path(api_client, db_session) -> None:
    _, old_id, new_id = _pair(db_session)
    r = api_client.post(
        f"/claims/{old_id}/supersede",
        json={"actor": "alice", "new_claim_id": str(new_id), "note": "self-correction"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == str(old_id)
    assert body["status"] == "superseded"
    assert body["superseded_by"] == str(new_id)
    assert body["text"] == "Subject moved in 1961.", "old text must NOT be rewritten"


def test_supersede_404_for_missing_old(api_client) -> None:
    r = api_client.post(
        f"/claims/{uuid.uuid4()}/supersede",
        json={"actor": "alice", "new_claim_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404


def test_supersede_404_for_missing_new(api_client, db_session) -> None:
    _, old_id, _ = _pair(db_session)
    r = api_client.post(
        f"/claims/{old_id}/supersede",
        json={"actor": "alice", "new_claim_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404


def test_supersede_self_returns_422(api_client, db_session) -> None:
    _, old_id, _ = _pair(db_session)
    r = api_client.post(
        f"/claims/{old_id}/supersede",
        json={"actor": "alice", "new_claim_id": str(old_id)},
    )
    assert r.status_code == 422
    assert "itself" in r.json()["detail"]


def test_supersede_missing_actor_422(api_client, db_session) -> None:
    _, old_id, new_id = _pair(db_session)
    r = api_client.post(
        f"/claims/{old_id}/supersede",
        json={"new_claim_id": str(new_id)},
    )
    assert r.status_code == 422


def test_history_endpoint_returns_chain(api_client, db_session) -> None:
    _, old_id, new_id = _pair(db_session)
    api_client.post(
        f"/claims/{old_id}/supersede",
        json={"actor": "alice", "new_claim_id": str(new_id), "note": "fixed year"},
    )
    r = api_client.get(f"/claims/{old_id}/history")
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 2
    assert entries[0]["claim"]["id"] == str(old_id)
    assert entries[0]["claim"]["text"] == "Subject moved in 1961."
    assert entries[0]["superseded_by_actor"] == "alice"
    assert entries[0]["note"] == "fixed year"
    assert entries[0]["superseded_at"] is not None
    assert entries[1]["claim"]["id"] == str(new_id)
    assert entries[1]["superseded_at"] is None


def test_history_404_for_unknown(api_client) -> None:
    r = api_client.get(f"/claims/{uuid.uuid4()}/history")
    assert r.status_code == 404


def test_supersede_audit_visible_in_log_endpoint(api_client, db_session) -> None:
    _, old_id, new_id = _pair(db_session)
    api_client.post(
        f"/claims/{old_id}/supersede",
        json={"actor": "alice", "new_claim_id": str(new_id)},
    )
    log = api_client.get(f"/claims/{old_id}/log").json()
    assert [e["action"] for e in log] == ["supersede"]
    assert log[0]["payload"]["new_claim_id"] == str(new_id)
