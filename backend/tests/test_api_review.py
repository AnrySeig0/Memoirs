"""HTTP-level tests for the M3 Review API.

We exercise the endpoints with FastAPI's TestClient. The `api_client`
fixture (in conftest.py) overrides `get_db` to bind requests to the
test engine, so HTTP commits land in the same Postgres the `db_session`
fixture sees.
"""
import uuid

from app.services.ingest import Turn, ingest_text_transcript
from app.store import insert_claim_with_sources


def _seed(db_session, *, status: str = "pending") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert a session + claim, return (subject_id, claim_id, utterance_id)."""
    subject_id = uuid.uuid4()
    ingest = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=1,
        turns=[Turn("subject", "Năm 1962 tôi chuyển đến Detroit.")],
        storage_uri="s3://memoir/test/m3_api.txt",
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
    return subject_id, claim.id, ingest.utterance_ids[0]


def test_healthz(api_client) -> None:
    r = api_client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_get_claims_returns_grounding_inline(api_client, db_session) -> None:
    """§1 'hiển thị cạnh câu gốc' — one round trip surfaces both."""
    subject_id, claim_id, utterance_id = _seed(db_session)

    r = api_client.get("/claims", params={"status": "pending", "subject_id": str(subject_id)})
    assert r.status_code == 200
    payload = r.json()
    assert len(payload) == 1
    item = payload[0]
    assert item["id"] == str(claim_id)
    assert item["status"] == "pending"
    assert len(item["sources"]) == 1
    assert item["sources"][0]["id"] == str(utterance_id)
    assert "1962" in item["sources"][0]["text"]


def test_get_claim_404_for_unknown(api_client) -> None:
    r = api_client.get(f"/claims/{uuid.uuid4()}")
    assert r.status_code == 404


def test_accept_changes_status_and_logs(api_client, db_session) -> None:
    _, claim_id, _ = _seed(db_session)
    r = api_client.post(f"/claims/{claim_id}/accept", json={"actor": "alice"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    assert r.json()["reviewed_by"] == "alice"

    log = api_client.get(f"/claims/{claim_id}/log").json()
    assert [entry["action"] for entry in log] == ["accept"]


def test_reject_with_reason_audited(api_client, db_session) -> None:
    _, claim_id, _ = _seed(db_session)
    r = api_client.post(
        f"/claims/{claim_id}/reject",
        json={"actor": "alice", "reason": "duplicate"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    log = api_client.get(f"/claims/{claim_id}/log").json()
    assert log[0]["action"] == "reject"
    assert log[0]["payload"] == {"reason": "duplicate"}


def test_edit_writes_previous_text(api_client, db_session) -> None:
    _, claim_id, _ = _seed(db_session)
    r = api_client.post(
        f"/claims/{claim_id}/edit",
        json={"actor": "alice", "text": "Detroit, MI in 1962."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "Detroit, MI in 1962."
    assert body["status"] == "edited"

    log = api_client.get(f"/claims/{claim_id}/log").json()
    assert log[0]["action"] == "edit"
    assert log[0]["payload"]["previous_text"] == "Subject moved to Detroit in 1962."
    assert log[0]["payload"]["new_text"] == "Detroit, MI in 1962."


def test_flag_audited(api_client, db_session) -> None:
    _, claim_id, _ = _seed(db_session)
    r = api_client.post(
        f"/claims/{claim_id}/flag",
        json={"actor": "alice", "reason": "ambiguous date"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "flagged"

    log = api_client.get(f"/claims/{claim_id}/log").json()
    assert log[0]["action"] == "flag"
    assert log[0]["payload"]["reason"] == "ambiguous date"


def test_action_on_missing_claim_returns_404(api_client) -> None:
    r = api_client.post(
        f"/claims/{uuid.uuid4()}/accept", json={"actor": "alice"}
    )
    assert r.status_code == 404


def test_missing_actor_rejected_by_pydantic(api_client, db_session) -> None:
    _, claim_id, _ = _seed(db_session)
    r = api_client.post(f"/claims/{claim_id}/accept", json={})
    assert r.status_code == 422


def test_edit_on_superseded_returns_422(api_client, db_session) -> None:
    """A superseded claim must be reached via a real supersede chain
    (M4 invariant CHECK). Build the chain via API, then try to edit
    the historic claim.
    """
    subject_id, old_id, _ = _seed(db_session)
    # Create a successor and supersede via HTTP — exercises the same
    # surface the editor would use.
    second = ingest_text_transcript(
        db_session,
        subject_id=subject_id,
        session_no=2,
        turns=[Turn("subject", "Năm 1963 mới đúng.")],
        storage_uri="s3://memoir/test/m3_edit_super_api.txt",
    )
    new_claim = insert_claim_with_sources(
        db_session,
        subject_id=subject_id,
        text="Subject moved in 1963.",
        claim_type="event",
        confidence=0.7,
        source_utterance_ids=list(second.utterance_ids),
    )
    db_session.commit()
    api_client.post(
        f"/claims/{old_id}/supersede",
        json={"actor": "alice", "new_claim_id": str(new_claim.id)},
    )

    r = api_client.post(
        f"/claims/{old_id}/edit",
        json={"actor": "alice", "text": "trying to rewrite history"},
    )
    assert r.status_code == 422
    assert "superseded" in r.json()["detail"]


def test_unknown_status_filter_returns_422(api_client) -> None:
    r = api_client.get("/claims", params={"status": "bogus"})
    assert r.status_code == 422


def test_log_for_unknown_claim_returns_404(api_client) -> None:
    r = api_client.get(f"/claims/{uuid.uuid4()}/log")
    assert r.status_code == 404


def test_two_actions_grow_the_log(api_client, db_session) -> None:
    """Reversal path: accept then reject. Log has both rows in order."""
    _, claim_id, _ = _seed(db_session)
    api_client.post(f"/claims/{claim_id}/accept", json={"actor": "alice"})
    api_client.post(
        f"/claims/{claim_id}/reject", json={"actor": "bob", "reason": "rechecked"}
    )
    log = api_client.get(f"/claims/{claim_id}/log").json()
    assert [entry["action"] for entry in log] == ["accept", "reject"]
    final = api_client.get(f"/claims/{claim_id}").json()
    assert final["status"] == "rejected" and final["reviewed_by"] == "bob"
