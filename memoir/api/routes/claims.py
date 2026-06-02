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
    ClaimHistoryEntry,
    ClaimOut,
    EditRequest,
    FlagRequest,
    MergeCandidateOut,
    MergeRequest,
    RejectRequest,
    ReviewLogOut,
    SupersedeRequest,
    UtteranceOut,
)
from memoir.resolve import DEFAULT_THRESHOLD, find_merge_candidates
from memoir.store import (
    VALID_CLAIM_STATUSES,
    Claim,
    ClaimNotFound,
    ClaimSource,
    ReviewLog,
    Utterance,
    accept_claim,
    claim_history,
    edit_claim,
    flag_claim,
    merge_claim,
    reject_claim,
    supersede_claim,
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


@router.get("/dedup-candidates", response_model=list[MergeCandidateOut])
def get_dedup_candidates(
    db: Annotated[OrmSession, Depends(get_db)],
    subject_id: Annotated[uuid.UUID, Query(description="Required — dedup is scoped per subject.")],
    threshold: Annotated[
        float, Query(ge=-1.0, le=1.0, description="Minimum cosine similarity to surface.")
    ] = DEFAULT_THRESHOLD,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[MergeCandidateOut]:
    """Surface merge candidates for `subject_id`. **Read-only.**

    §1 Merge safety rule: this endpoint NEVER commits a merge. It
    returns pairs; the editor decides via `POST /claims/{loser}/merge`.

    Declared BEFORE `GET /claims/{claim_id}` so FastAPI matches the
    literal path first — otherwise `dedup-candidates` would be parsed
    as a UUID-shaped claim_id and 422 out.
    """
    pairs = find_merge_candidates(
        db, subject_id=subject_id, threshold=threshold, limit=limit
    )
    return [
        MergeCandidateOut(
            claim_a_id=p.claim_a_id,
            claim_b_id=p.claim_b_id,
            similarity=p.similarity,
        )
        for p in pairs
    ]


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


@router.post("/{claim_id}/merge", response_model=ClaimOut)
def post_merge(
    claim_id: uuid.UUID,
    body: MergeRequest,
    db: Annotated[OrmSession, Depends(get_db)],
) -> ClaimOut:
    """Editor-confirmed merge: `claim_id` (the loser) is folded into
    `body.winner_claim_id`. The loser becomes superseded; its text is
    untouched. The audit log gets one `merge` row with similarity in
    payload.

    §1 Merge safety: this is the ONLY path that commits a merge.
    `GET /claims/dedup-candidates` only surfaces; the human picks here.
    """
    try:
        loser = merge_claim(
            db,
            loser_id=claim_id,
            winner_id=body.winner_claim_id,
            actor=body.actor,
            similarity=body.similarity,
            note=body.note,
        )
    except ClaimNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        )
    return _serialize(db, loser)


@router.post("/{claim_id}/supersede", response_model=ClaimOut)
def post_supersede(
    claim_id: uuid.UUID,
    body: SupersedeRequest,
    db: Annotated[OrmSession, Depends(get_db)],
) -> ClaimOut:
    """Mark `claim_id` (the old claim) as superseded by `body.new_claim_id`.

    §6 correction flow: the old claim's text stays untouched; only its
    status, `superseded_by`, and review metadata move. The audit log
    captures who confirmed the correction and when.
    """
    try:
        old_claim = supersede_claim(
            db,
            old_id=claim_id,
            new_id=body.new_claim_id,
            actor=body.actor,
            note=body.note,
        )
    except ClaimNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        )
    return _serialize(db, old_claim)


@router.get("/{claim_id}/history", response_model=list[ClaimHistoryEntry])
def get_claim_history(
    claim_id: uuid.UUID,
    db: Annotated[OrmSession, Depends(get_db)],
) -> list[ClaimHistoryEntry]:
    """Return the full correction chain that contains `claim_id`.

    Order is chronological (root → leaf). Each non-leaf entry includes
    the timestamp / actor / note from the supersede audit row that
    closed it — the §6 "đã nói gì → sửa thành gì → khi nào" trail.
    """
    try:
        entries = claim_history(db, claim_id=claim_id)
    except ClaimNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found")
    return [
        ClaimHistoryEntry(
            claim=_serialize(db, entry.claim),
            superseded_at=entry.superseded_at,
            superseded_by_actor=entry.superseded_by_actor,
            note=entry.note,
        )
        for entry in entries
    ]
