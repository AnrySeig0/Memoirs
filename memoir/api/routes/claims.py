"""Review UI endpoints — the operational surface for M3.

`GET /claims` returns each claim alongside the utterances it cites, so
the editor renders both halves of "claim + câu gốc" from one round
trip. State changes funnel through repository functions that update the
claim AND write a `review_log` row in the same transaction — there is
no path that mutates a claim without auditing.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from memoir.api.deps import get_db
from memoir.api.schemas import (
    AcceptRequest,
    ClaimOut,
    EditRequest,
    FlagRequest,
    RejectRequest,
    ReviewLogOut,
    UtteranceOut,
)
from memoir.store import (
    VALID_CLAIM_STATUSES,
    Claim,
    ClaimNotFound,
    ClaimSource,
    ReviewLog,
    Utterance,
    accept_claim,
    edit_claim,
    flag_claim,
    reject_claim,
)

router = APIRouter(prefix="/claims", tags=["claims"])


def _serialize(db: OrmSession, claim: Claim) -> ClaimOut:
    utterances = (
        db.execute(
            select(Utterance)
            .join(ClaimSource, ClaimSource.utterance_id == Utterance.id)
            .where(ClaimSource.claim_id == claim.id)
            .order_by(Utterance.char_start)
        )
        .scalars()
        .all()
    )
    return ClaimOut(
        id=claim.id,
        subject_id=claim.subject_id,
        text=claim.text,
        claim_type=claim.claim_type,
        confidence=claim.confidence,
        status=claim.status,
        superseded_by=claim.superseded_by,
        created_at=claim.created_at,
        reviewed_at=claim.reviewed_at,
        reviewed_by=claim.reviewed_by,
        sources=[UtteranceOut.model_validate(u) for u in utterances],
    )


def _resolve_action(action_callable, db: OrmSession, claim_id: uuid.UUID, **kwargs) -> ClaimOut:
    try:
        claim = action_callable(db, claim_id=claim_id, **kwargs)
    except ClaimNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    except ValueError as exc:
        # ValueError covers domain refusals like "cannot edit a superseded
        # claim"; 422 is the right shape — the request is well-formed but
        # violates the claim's lifecycle invariants.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        )
    return _serialize(db, claim)


@router.get("", response_model=list[ClaimOut])
def list_claims(
    db: Annotated[OrmSession, Depends(get_db)],
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
    if status_filter is not None and status_filter not in VALID_CLAIM_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown status {status_filter!r}",
        )
    stmt = select(Claim).order_by(Claim.created_at)
    if status_filter is not None:
        stmt = stmt.where(Claim.status == status_filter)
    if subject_id is not None:
        stmt = stmt.where(Claim.subject_id == subject_id)
    stmt = stmt.limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [_serialize(db, c) for c in rows]


@router.get("/{claim_id}", response_model=ClaimOut)
def get_claim(
    claim_id: uuid.UUID,
    db: Annotated[OrmSession, Depends(get_db)],
) -> ClaimOut:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    return _serialize(db, claim)


@router.post("/{claim_id}/accept", response_model=ClaimOut)
def post_accept(
    claim_id: uuid.UUID,
    body: AcceptRequest,
    db: Annotated[OrmSession, Depends(get_db)],
) -> ClaimOut:
    return _resolve_action(accept_claim, db, claim_id, actor=body.actor)


@router.post("/{claim_id}/reject", response_model=ClaimOut)
def post_reject(
    claim_id: uuid.UUID,
    body: RejectRequest,
    db: Annotated[OrmSession, Depends(get_db)],
) -> ClaimOut:
    return _resolve_action(
        reject_claim, db, claim_id, actor=body.actor, reason=body.reason
    )


@router.post("/{claim_id}/edit", response_model=ClaimOut)
def post_edit(
    claim_id: uuid.UUID,
    body: EditRequest,
    db: Annotated[OrmSession, Depends(get_db)],
) -> ClaimOut:
    return _resolve_action(
        edit_claim, db, claim_id, actor=body.actor, new_text=body.text
    )


@router.post("/{claim_id}/flag", response_model=ClaimOut)
def post_flag(
    claim_id: uuid.UUID,
    body: FlagRequest,
    db: Annotated[OrmSession, Depends(get_db)],
) -> ClaimOut:
    return _resolve_action(
        flag_claim, db, claim_id, actor=body.actor, reason=body.reason
    )


@router.get("/{claim_id}/log", response_model=list[ReviewLogOut])
def get_review_log(
    claim_id: uuid.UUID,
    db: Annotated[OrmSession, Depends(get_db)],
) -> list[ReviewLogOut]:
    if db.get(Claim, claim_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    rows = (
        db.execute(
            select(ReviewLog)
            .where(ReviewLog.claim_id == claim_id)
            .order_by(ReviewLog.created_at)
        )
        .scalars()
        .all()
    )
    return [ReviewLogOut.model_validate(r) for r in rows]
