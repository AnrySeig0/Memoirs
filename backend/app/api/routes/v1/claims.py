"""Review UI endpoints — the operational surface for M3.

Routes are thin: they validate the request shape (Pydantic) and delegate to
`ClaimService`. Domain failures (`ClaimNotFound` → 404, `ClaimLifecycleError`
→ 422) propagate to the registered exception handler — no route raises
`HTTPException` itself. Every state change funnels through the service into
repository functions that update the claim AND write a `review_log` row in
the same transaction.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ClaimSvc
from app.schemas.claim import (
    AcceptRequest,
    ClaimHistoryEntry,
    ClaimOut,
    EditRequest,
    FlagRequest,
    MergeCandidateOut,
    MergeRequest,
    RejectRequest,
    SupersedeRequest,
)
from app.schemas.review import ReviewLogOut
from app.services.resolve import DEFAULT_THRESHOLD
from app.repositories.claim import VALID_CLAIM_STATUSES

router = APIRouter(prefix="/claims", tags=["claims"])


@router.get("/dedup-candidates", response_model=list[MergeCandidateOut])
def get_dedup_candidates(
    svc: ClaimSvc,
    subject_id: Annotated[uuid.UUID, Query(description="Required — dedup is scoped per subject.")],
    threshold: Annotated[
        float, Query(ge=-1.0, le=1.0, description="Minimum cosine similarity to surface.")
    ] = DEFAULT_THRESHOLD,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[MergeCandidateOut]:
    """Surface merge candidates for `subject_id`. **Read-only.**

    §1 Merge safety rule: this endpoint NEVER commits a merge. It returns
    pairs; the editor decides via `POST /claims/{loser}/merge`.

    Declared BEFORE `GET /claims/{claim_id}` so FastAPI matches the literal
    path first — otherwise `dedup-candidates` would be parsed as a
    UUID-shaped claim_id and 422 out.
    """
    return svc.dedup_candidates(subject_id=subject_id, threshold=threshold, limit=limit)


@router.get("", response_model=list[ClaimOut])
def list_claims(
    svc: ClaimSvc,
    status_filter: Annotated[
        str | None,
        Query(
            alias="status",
            description=(
                f"Filter by claim status. One of: {sorted(VALID_CLAIM_STATUSES)}. "
                "Omit to list all statuses."
            ),
        ),
    ] = "pending",
    subject_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ClaimOut]:
    return svc.list_claims(
        status_filter=status_filter, subject_id=subject_id, limit=limit, offset=offset
    )


@router.get("/{claim_id}", response_model=ClaimOut)
def get_claim(claim_id: uuid.UUID, svc: ClaimSvc) -> ClaimOut:
    return svc.get_claim(claim_id)


@router.post("/{claim_id}/accept", response_model=ClaimOut)
def post_accept(claim_id: uuid.UUID, body: AcceptRequest, svc: ClaimSvc) -> ClaimOut:
    return svc.accept(claim_id, actor=body.actor)


@router.post("/{claim_id}/reject", response_model=ClaimOut)
def post_reject(claim_id: uuid.UUID, body: RejectRequest, svc: ClaimSvc) -> ClaimOut:
    return svc.reject(claim_id, actor=body.actor, reason=body.reason)


@router.post("/{claim_id}/edit", response_model=ClaimOut)
def post_edit(claim_id: uuid.UUID, body: EditRequest, svc: ClaimSvc) -> ClaimOut:
    return svc.edit(claim_id, actor=body.actor, new_text=body.text)


@router.post("/{claim_id}/flag", response_model=ClaimOut)
def post_flag(claim_id: uuid.UUID, body: FlagRequest, svc: ClaimSvc) -> ClaimOut:
    return svc.flag(claim_id, actor=body.actor, reason=body.reason)


@router.get("/{claim_id}/log", response_model=list[ReviewLogOut])
def get_review_log(claim_id: uuid.UUID, svc: ClaimSvc) -> list[ReviewLogOut]:
    return svc.review_log(claim_id)


@router.post("/{claim_id}/merge", response_model=ClaimOut)
def post_merge(claim_id: uuid.UUID, body: MergeRequest, svc: ClaimSvc) -> ClaimOut:
    """Editor-confirmed merge: `claim_id` (the loser) is folded into
    `body.winner_claim_id`. The loser becomes superseded; its text is
    untouched. The audit log gets one `merge` row with similarity in payload.

    §1 Merge safety: this is the ONLY path that commits a merge.
    `GET /claims/dedup-candidates` only surfaces; the human picks here.
    """
    return svc.merge(
        claim_id,
        winner_id=body.winner_claim_id,
        actor=body.actor,
        similarity=body.similarity,
        note=body.note,
    )


@router.post("/{claim_id}/supersede", response_model=ClaimOut)
def post_supersede(
    claim_id: uuid.UUID, body: SupersedeRequest, svc: ClaimSvc
) -> ClaimOut:
    """Mark `claim_id` (the old claim) as superseded by `body.new_claim_id`.

    §6 correction flow: the old claim's text stays untouched; only its
    status, `superseded_by`, and review metadata move. The audit log
    captures who confirmed the correction and when.
    """
    return svc.supersede(
        claim_id, new_id=body.new_claim_id, actor=body.actor, note=body.note
    )


@router.get("/{claim_id}/history", response_model=list[ClaimHistoryEntry])
def get_claim_history(claim_id: uuid.UUID, svc: ClaimSvc) -> list[ClaimHistoryEntry]:
    """Return the full correction chain that contains `claim_id`.

    Order is chronological (root → leaf). Each non-leaf entry includes the
    timestamp / actor / note from the supersede audit row that closed it —
    the §6 "đã nói gì → sửa thành gì → khi nào" trail.
    """
    return svc.history(claim_id)
