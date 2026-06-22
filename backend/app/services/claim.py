"""Business logic for the Review UI surface (M3–M5).

`ClaimService` is the only thing routes talk to. It orchestrates the claim
repository + the dedup query, serializes claims alongside their grounding
utterances (§1 "hiển thị cạnh câu gốc"), and raises domain exceptions
(`ClaimNotFound` → 404, `ClaimLifecycleError` → 422) which the API
exception handler turns into HTTP responses. Routes carry no try/except.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.db.models import Claim, ClaimSource, ReviewLog, Utterance
from app.repositories.claim import (
    VALID_CLAIM_STATUSES,
    ClaimLifecycleError,
    ClaimNotFound,
    accept_claim,
    claim_history,
    edit_claim,
    flag_claim,
    merge_claim,
    reject_claim,
    supersede_claim,
)
from app.schemas.claim import (
    ClaimHistoryEntry,
    ClaimOut,
    MergeCandidateOut,
)
from app.schemas.review import ReviewLogOut
from app.schemas.utterance import UtteranceOut
from app.services.resolve import find_merge_candidates


class ClaimService:
    def __init__(self, db: OrmSession) -> None:
        self.db = db

    # --- serialization ----------------------------------------------------

    def serialize(self, claim: Claim) -> ClaimOut:
        """Build a `ClaimOut` with the utterances the claim cites inline."""
        utterances = (
            self.db.execute(
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

    def _get_or_raise(self, claim_id: uuid.UUID) -> Claim:
        claim = self.db.get(Claim, claim_id)
        if claim is None:
            raise ClaimNotFound(f"claim {claim_id} not found")
        return claim

    # --- reads ------------------------------------------------------------

    def list_claims(
        self,
        *,
        status_filter: str | None = "pending",
        subject_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ClaimOut]:
        if status_filter is not None and status_filter not in VALID_CLAIM_STATUSES:
            raise ClaimLifecycleError(f"unknown status {status_filter!r}")
        stmt = select(Claim).order_by(Claim.created_at)
        if status_filter is not None:
            stmt = stmt.where(Claim.status == status_filter)
        if subject_id is not None:
            stmt = stmt.where(Claim.subject_id == subject_id)
        stmt = stmt.limit(limit).offset(offset)
        rows = self.db.execute(stmt).scalars().all()
        return [self.serialize(c) for c in rows]

    def get_claim(self, claim_id: uuid.UUID) -> ClaimOut:
        return self.serialize(self._get_or_raise(claim_id))

    def review_log(self, claim_id: uuid.UUID) -> list[ReviewLogOut]:
        self._get_or_raise(claim_id)
        rows = (
            self.db.execute(
                select(ReviewLog)
                .where(ReviewLog.claim_id == claim_id)
                .order_by(ReviewLog.created_at)
            )
            .scalars()
            .all()
        )
        return [ReviewLogOut.model_validate(r) for r in rows]

    def history(self, claim_id: uuid.UUID) -> list[ClaimHistoryEntry]:
        entries = claim_history(self.db, claim_id=claim_id)
        return [
            ClaimHistoryEntry(
                claim=self.serialize(entry.claim),
                superseded_at=entry.superseded_at,
                superseded_by_actor=entry.superseded_by_actor,
                note=entry.note,
            )
            for entry in entries
        ]

    def dedup_candidates(
        self,
        *,
        subject_id: uuid.UUID,
        threshold: float,
        limit: int,
    ) -> list[MergeCandidateOut]:
        """Surface merge candidates for `subject_id`. Read-only — surfacing a
        candidate is NOT a merge (§1 merge-safety rule).
        """
        pairs = find_merge_candidates(
            self.db, subject_id=subject_id, threshold=threshold, limit=limit
        )
        return [
            MergeCandidateOut(
                claim_a_id=p.claim_a_id,
                claim_b_id=p.claim_b_id,
                similarity=p.similarity,
            )
            for p in pairs
        ]

    # --- review actions (each writes a review_log row in-transaction) ------

    def accept(self, claim_id: uuid.UUID, *, actor: str) -> ClaimOut:
        return self.serialize(accept_claim(self.db, claim_id=claim_id, actor=actor))

    def reject(
        self, claim_id: uuid.UUID, *, actor: str, reason: str | None = None
    ) -> ClaimOut:
        return self.serialize(
            reject_claim(self.db, claim_id=claim_id, actor=actor, reason=reason)
        )

    def edit(self, claim_id: uuid.UUID, *, actor: str, new_text: str) -> ClaimOut:
        return self.serialize(
            edit_claim(self.db, claim_id=claim_id, actor=actor, new_text=new_text)
        )

    def flag(
        self, claim_id: uuid.UUID, *, actor: str, reason: str | None = None
    ) -> ClaimOut:
        return self.serialize(
            flag_claim(self.db, claim_id=claim_id, actor=actor, reason=reason)
        )

    def merge(
        self,
        loser_id: uuid.UUID,
        *,
        winner_id: uuid.UUID,
        actor: str,
        similarity: float | None = None,
        note: str | None = None,
    ) -> ClaimOut:
        loser = merge_claim(
            self.db,
            loser_id=loser_id,
            winner_id=winner_id,
            actor=actor,
            similarity=similarity,
            note=note,
        )
        return self.serialize(loser)

    def supersede(
        self,
        old_id: uuid.UUID,
        *,
        new_id: uuid.UUID,
        actor: str,
        note: str | None = None,
    ) -> ClaimOut:
        old = supersede_claim(
            self.db, old_id=old_id, new_id=new_id, actor=actor, note=note
        )
        return self.serialize(old)
