"""HTTP-level tests for the M5 dedup-candidates + merge endpoints."""
import uuid

from app.services.ingest import Turn, ingest_text_transcript
from app.services.resolve import DeterministicEmbedder
from app.store import insert_claim_with_sources, set_claim_embedding


def _seed_pair(db_session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (subject_id, claim_a_id, claim_b_id) — two identical-text
    embedded claims that will surface as a dedup candidate above any
    reasonable threshold.
    """
    subject_id = uuid.uuid4()
    embedder = DeterministicEmbedder()
    text = "Subject moved to Detroit in 1962."
    ids = []
    for i in range(2):
        ingest = ingest_text_transcript(
            db_session,
            subject_id=subject_id,
            session_no=i + 1,
            turns=[Turn("subject", text)],
            storage_uri=f"s3://memoir/test/m5_api_{i}.txt",
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
    return subject_id, ids[0], ids[1]


def test_dedup_candidates_endpoint_returns_pair(api_client, db_session) -> None:
    subject_id, a_id, b_id = _seed_pair(db_session)
    r = api_client.get(
        "/claims/dedup-candidates",
        params={"subject_id": str(subject_id), "threshold": 0.9},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    surfaced = {body[0]["claim_a_id"], body[0]["claim_b_id"]}
    assert surfaced == {str(a_id), str(b_id)}
    assert body[0]["similarity"] >= 0.9


def test_dedup_candidates_does_not_route_to_claim_path(api_client) -> None:
    """`/claims/dedup-candidates` must not be parsed as `/claims/{uuid}`.

    If route ordering regresses, the request returns 422 (UUID parse
    fail) instead of 200 with an empty list.
    """
    r = api_client.get(
        "/claims/dedup-candidates", params={"subject_id": str(uuid.uuid4())}
    )
    assert r.status_code == 200
    assert r.json() == []


def test_dedup_threshold_validation(api_client) -> None:
    r = api_client.get(
        "/claims/dedup-candidates",
        params={"subject_id": str(uuid.uuid4()), "threshold": 1.5},
    )
    assert r.status_code == 422


def test_merge_endpoint_supersedes_loser(api_client, db_session) -> None:
    _, loser_id, winner_id = _seed_pair(db_session)
    r = api_client.post(
        f"/claims/{loser_id}/merge",
        json={
            "actor": "alice",
            "winner_claim_id": str(winner_id),
            "similarity": 1.0,
            "note": "obvious duplicate",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "superseded"
    assert body["superseded_by"] == str(winner_id)
    assert body["text"] == "Subject moved to Detroit in 1962."

    log = api_client.get(f"/claims/{loser_id}/log").json()
    assert log[0]["action"] == "merge"
    assert log[0]["payload"]["similarity"] == 1.0


def test_merge_self_returns_422(api_client, db_session) -> None:
    _, a_id, _ = _seed_pair(db_session)
    r = api_client.post(
        f"/claims/{a_id}/merge",
        json={"actor": "alice", "winner_claim_id": str(a_id)},
    )
    assert r.status_code == 422
    assert "itself" in r.json()["detail"]


def test_merge_unknown_returns_404(api_client) -> None:
    r = api_client.post(
        f"/claims/{uuid.uuid4()}/merge",
        json={"actor": "alice", "winner_claim_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404


def test_merge_after_merge_surfaces_no_more_candidate(api_client, db_session) -> None:
    """Round-trip: surface a candidate, merge it, candidate disappears."""
    subject_id, loser_id, winner_id = _seed_pair(db_session)
    pre = api_client.get(
        "/claims/dedup-candidates",
        params={"subject_id": str(subject_id), "threshold": 0.9},
    ).json()
    assert len(pre) == 1

    api_client.post(
        f"/claims/{loser_id}/merge",
        json={"actor": "alice", "winner_claim_id": str(winner_id)},
    )

    post = api_client.get(
        "/claims/dedup-candidates",
        params={"subject_id": str(subject_id), "threshold": 0.9},
    ).json()
    assert post == []
