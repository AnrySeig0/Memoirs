"""Repository for grounded claims + the M3/M4/M5 review lifecycle.

The M2 contract for `insert_claim_with_sources` is the load-bearing one:
a claim without ≥1 source utterance cannot reach the DB, because the claim
row and its `claim_sources` rows are written in the same transaction by a
single function.

Exceptions
----------
- `ClaimNotFound` (404) — the claim id does not resolve.
- `ClaimLifecycleError` (422) — a well-formed request that violates a claim
  invariant (e.g. editing a superseded claim, supersede across subjects).
  It subclasses both `ValidationError` (so the API handler returns 422) and
  `ValueError` (so existing call sites / tests that expect a `ValueError`
  keep working).
"""
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import EMBEDDING_DIM, Claim, ClaimSource, ReviewLog
from app.repositories.review_log import VALID_REVIEW_ACTIONS, insert_review_log

VALID_CLAIM_STATUSES = frozenset(
    {"pending", "accepted", "rejected", "edited", "flagged", "superseded"}
)


class ClaimNotFound(NotFoundError):
    """The claim id does not resolve — the API layer returns 404."""


class ClaimLifecycleError(ValidationError, ValueError):
    """A claim invariant was violated by a well-formed request (422).

    Also a `ValueError` so call sites that historically caught `ValueError`
    (and the repository tests) continue to work unchanged.
    """


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def insert_claim_with_sources(
    db: OrmSession,
    *,
    subject_id: uuid.UUID,
    text: str,
    claim_type: str | None,
    confidence: float,
    source_utterance_ids: Sequence[uuid.UUID],
    status: str = "pending",
) -> Claim:
    """Atomically insert a claim and its grounding rows.

    The M2 hard rule: a claim with zero source utterances is rejected
    before any row is written. We do this at the code layer because
    "≥1 row exists in claim_sources" can't be expressed as a single
    column constraint; the §4 README is explicit that this is enforced
    in code.

    If any grounding insert fails (e.g. a bad utterance_id FK), the
    surrounding transaction must be rolled back by the caller — the
    claim row would otherwise dangle without sources.
    """
    if not source_utterance_ids:
        raise ClaimLifecycleError(
            "claim requires at least one source utterance (M2 grounding rule)"
        )
    if not 0.0 <= confidence <= 1.0:
        raise ClaimLifecycleError(f"confidence must be in [0,1], got {confidence}")
    if status not in VALID_CLAIM_STATUSES:
        raise ClaimLifecycleError(
            f"status must be one of {sorted(VALID_CLAIM_STATUSES)}, got {status!r}"
        )
    # Dedup defensive — passing the same utterance twice would violate the
    # composite PK on claim_sources and abort the transaction.
    unique_ids = list(dict.fromkeys(source_utterance_ids))

    claim = Claim(
        subject_id=subject_id,
        text=text,
        claim_type=claim_type,
        confidence=confidence,
        status=status,
    )
    db.add(claim)
    db.flush()
    for utterance_id in unique_ids:
        db.add(ClaimSource(claim_id=claim.id, utterance_id=utterance_id))
    db.flush()
    return claim


# ---------------------------------------------------------------------------
# M3: review actions
# ---------------------------------------------------------------------------
#
# Each review action mutates the claim row AND writes a review_log row in
# the same transaction. The audit row is what makes the decision
# reversible — §1 "đảo ngược được" — even after the claim's surface state
# moves on. Editors are allowed to disagree with themselves; the log
# gains another row, no row is rewritten.
#
# `edit` is the only action that mutates `claim.text`. It captures the
# previous text in the audit payload so the old wording is recoverable.
# This is editor-edit (rephrasing for clarity), distinct from §6
# subject-correction which becomes a NEW claim via supersede (M4).


def _load_claim_or_raise(db: OrmSession, claim_id: uuid.UUID) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise ClaimNotFound(f"claim {claim_id} not found")
    return claim


def _record_review(
    db: OrmSession,
    *,
    claim: Claim,
    actor: str,
    action: str,
    new_status: str,
    payload: dict[str, Any] | None = None,
) -> tuple[Claim, ReviewLog]:
    if action not in VALID_REVIEW_ACTIONS:
        raise ClaimLifecycleError(
            f"action must be one of {sorted(VALID_REVIEW_ACTIONS)}, got {action!r}"
        )
    if new_status not in VALID_CLAIM_STATUSES:
        raise ClaimLifecycleError(
            f"status must be one of {sorted(VALID_CLAIM_STATUSES)}, got {new_status!r}"
        )
    if not actor or not actor.strip():
        raise ClaimLifecycleError("actor must not be empty")

    now = _utcnow()
    claim.status = new_status
    claim.reviewed_at = now
    claim.reviewed_by = actor
    db.flush()

    log = insert_review_log(
        db, claim_id=claim.id, action=action, actor=actor, payload=payload
    )
    return claim, log


def accept_claim(db: OrmSession, *, claim_id: uuid.UUID, actor: str) -> Claim:
    claim = _load_claim_or_raise(db, claim_id)
    if claim.status == "superseded":
        raise ClaimLifecycleError(
            "cannot accept a superseded claim — its successor carries the "
            "current narrative (M4 supersede flow)"
        )
    claim, _ = _record_review(
        db, claim=claim, actor=actor, action="accept", new_status="accepted"
    )
    return claim


def reject_claim(
    db: OrmSession,
    *,
    claim_id: uuid.UUID,
    actor: str,
    reason: str | None = None,
) -> Claim:
    claim = _load_claim_or_raise(db, claim_id)
    payload: dict[str, Any] | None = {"reason": reason} if reason is not None else None
    claim, _ = _record_review(
        db,
        claim=claim,
        actor=actor,
        action="reject",
        new_status="rejected",
        payload=payload,
    )
    return claim


def edit_claim(
    db: OrmSession,
    *,
    claim_id: uuid.UUID,
    actor: str,
    new_text: str,
) -> Claim:
    if not new_text or not new_text.strip():
        raise ClaimLifecycleError("new_text must not be empty")
    claim = _load_claim_or_raise(db, claim_id)
    if claim.status == "superseded":
        raise ClaimLifecycleError(
            "cannot edit a superseded claim — use the M4 supersede flow to "
            "add a new claim instead of mutating the historic one"
        )
    previous_text = claim.text
    claim.text = new_text
    payload = {"previous_text": previous_text, "new_text": new_text}
    claim, _ = _record_review(
        db,
        claim=claim,
        actor=actor,
        action="edit",
        new_status="edited",
        payload=payload,
    )
    return claim


def flag_claim(
    db: OrmSession,
    *,
    claim_id: uuid.UUID,
    actor: str,
    reason: str | None = None,
) -> Claim:
    claim = _load_claim_or_raise(db, claim_id)
    payload: dict[str, Any] | None = {"reason": reason} if reason is not None else None
    claim, _ = _record_review(
        db,
        claim=claim,
        actor=actor,
        action="flag",
        new_status="flagged",
        payload=payload,
    )
    return claim


# ---------------------------------------------------------------------------
# M4: correction / supersede
# ---------------------------------------------------------------------------
#
# §6 flow: when the subject says in a later session "actually it was '62,
# not '61", we DO NOT overwrite the old claim. Instead we mark it
# superseded and link it to the new claim that carries the corrected
# statement. The old text stays exactly as it was — drift becomes visible
# rather than vanishing.
#
# Invariants enforced here:
# - old.id != new.id (no self-supersede)
# - old.subject_id == new.subject_id (no cross-subject corrections)
# - old.status != 'superseded' (must supersede the leaf, not a historic node)
# - new.status != 'superseded' (the corrector itself must be live)
# - new is not already the successor of some other claim (1:1 supersede;
#   many-to-one is "merge", which is M5's concern)
#
# old.text is never touched. The only fields mutated on old are status,
# superseded_by, reviewed_at, reviewed_by. A single `review_log` row with
# action='supersede' records who confirmed the correction and when.


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One link in a claim's correction chain.

    `superseded_at` / `superseded_by_actor` / `note` come from the
    `review_log` row that marked this claim's transition to superseded.
    They are `None` for the leaf — the still-current claim hasn't been
    superseded by anything yet.
    """

    claim: "Claim"
    superseded_at: datetime | None
    superseded_by_actor: str | None
    note: str | None


def supersede_claim(
    db: OrmSession,
    *,
    old_id: uuid.UUID,
    new_id: uuid.UUID,
    actor: str,
    note: str | None = None,
) -> Claim:
    """Mark `old_id` as superseded by `new_id`. Returns the updated old claim.

    Raises:
        ClaimNotFound: if either id does not resolve.
        ClaimLifecycleError: if a supersede invariant is violated. The API
            layer translates this into 422.
    """
    if not actor or not actor.strip():
        raise ClaimLifecycleError("actor must not be empty")
    if old_id == new_id:
        raise ClaimLifecycleError("cannot supersede a claim with itself")

    old = _load_claim_or_raise(db, old_id)
    new = _load_claim_or_raise(db, new_id)

    if old.subject_id != new.subject_id:
        raise ClaimLifecycleError(
            "cannot supersede across subjects — old and new claims belong "
            "to different subjects"
        )
    if old.status == "superseded":
        raise ClaimLifecycleError(
            f"claim {old_id} is already superseded — supersede the leaf of "
            "its chain instead (use claim_history to find it)"
        )
    if new.status == "superseded":
        raise ClaimLifecycleError(
            f"new claim {new_id} is itself superseded — pick a live "
            "successor"
        )

    # 1:1 supersede invariant: the new claim must not already be the
    # target of some other supersede. Many-to-one (merge) is M5.
    existing_predecessor = db.execute(
        select(Claim.id).where(Claim.superseded_by == new.id)
    ).scalar_one_or_none()
    if existing_predecessor is not None:
        raise ClaimLifecycleError(
            f"new claim {new_id} is already the successor of claim "
            f"{existing_predecessor} — many-to-one supersede is a merge "
            "operation and belongs to M5"
        )

    # Bind successor BEFORE _record_review sets status='superseded',
    # because the DB CHECK enforces "(status='superseded') = (superseded_by
    # IS NOT NULL)" and SQLAlchemy may emit a single UPDATE for both.
    old.superseded_by = new.id
    payload: dict[str, Any] = {"new_claim_id": str(new.id)}
    if note is not None:
        payload["note"] = note

    old, _ = _record_review(
        db,
        claim=old,
        actor=actor,
        action="supersede",
        new_status="superseded",
        payload=payload,
    )
    return old


def claim_history(db: OrmSession, *, claim_id: uuid.UUID) -> list[HistoryEntry]:
    """Return the full correction chain that contains `claim_id`.

    Order is chronological: root (first thing said) at index 0, leaf
    (still-current claim) at the end. Each non-leaf entry carries the
    timestamp + actor + note of the `supersede` action that closed it.
    """
    target = _load_claim_or_raise(db, claim_id)

    # Walk backward to root via repeated "who points at me as successor?"
    # lookups. With ix_claims_superseded_by this is cheap.
    current = target
    visited: set[uuid.UUID] = {current.id}
    while True:
        prev = db.execute(
            select(Claim).where(Claim.superseded_by == current.id)
        ).scalar_one_or_none()
        if prev is None:
            break
        if prev.id in visited:
            # Defensive — a cycle should be impossible given our
            # invariants, but better to break than loop forever.
            break
        visited.add(prev.id)
        current = prev

    # Now `current` is the root; walk forward via superseded_by.
    chain: list[Claim] = [current]
    visited = {current.id}
    while chain[-1].superseded_by is not None:
        nxt = db.get(Claim, chain[-1].superseded_by)
        if nxt is None or nxt.id in visited:
            break
        visited.add(nxt.id)
        chain.append(nxt)

    # Look up the supersede audit row per non-leaf link.
    supersede_logs = db.execute(
        select(ReviewLog).where(
            ReviewLog.claim_id.in_([c.id for c in chain[:-1]]),
            ReviewLog.action == "supersede",
        )
    ).scalars().all()
    by_claim: dict[uuid.UUID, ReviewLog] = {
        log.claim_id: log for log in supersede_logs
    }

    entries: list[HistoryEntry] = []
    for idx, claim in enumerate(chain):
        if idx == len(chain) - 1:
            entries.append(HistoryEntry(claim=claim, superseded_at=None, superseded_by_actor=None, note=None))
        else:
            log = by_claim.get(claim.id)
            entries.append(
                HistoryEntry(
                    claim=claim,
                    superseded_at=log.created_at if log else None,
                    superseded_by_actor=log.actor if log else None,
                    note=(log.payload or {}).get("note") if log else None,
                )
            )
    return entries


# ---------------------------------------------------------------------------
# M5: embedding + entities + merge
# ---------------------------------------------------------------------------
#
# Merge is the deliberately-relaxed cousin of supersede. M4's
# `supersede_claim` enforces 1:1 (each new is the successor of at most one
# old) because subject corrections are inherently per-claim. Merge is
# many-to-one by design: an editor decides that several claims are
# different phrasings of the same fact and folds them into one winner.
#
# The mechanical update on a merged loser looks exactly like a supersede:
# loser.status='superseded', loser.superseded_by=winner.id, old text
# untouched. Only the audit row differs (action='merge' with similarity
# captured in payload) and the 1:1 invariant is relaxed.


def set_claim_embedding(
    db: OrmSession, *, claim_id: uuid.UUID, vector: Sequence[float]
) -> Claim:
    """Attach (or replace) the embedding vector for a claim.

    Refuses to embed a superseded claim — dead claims don't need to be
    in dedup queries, and the dedup SQL filters them out anyway.
    """
    if len(vector) != EMBEDDING_DIM:
        raise ClaimLifecycleError(
            f"embedding must be {EMBEDDING_DIM}-dim, got {len(vector)}"
        )
    claim = _load_claim_or_raise(db, claim_id)
    if claim.status == "superseded":
        raise ClaimLifecycleError(
            "cannot embed a superseded claim — its successor is what "
            "should appear in dedup candidates"
        )
    claim.embedding = list(vector)
    db.flush()
    return claim


def merge_claim(
    db: OrmSession,
    *,
    loser_id: uuid.UUID,
    winner_id: uuid.UUID,
    actor: str,
    similarity: float | None = None,
    note: str | None = None,
) -> Claim:
    """Editor-confirmed merge: `loser_id` is folded into `winner_id`.

    Mechanically equivalent to supersede (loser.status='superseded',
    loser.superseded_by=winner.id, loser.text untouched). Differences vs
    M4 supersede:
      - Audit row carries action='merge' + payload with similarity.
      - 1:1 invariant relaxed: a winner MAY already be the successor of
        other losers. Many-to-one merge is the whole point.

    Invariants still enforced:
      - actor non-empty
      - loser.id != winner.id
      - both exist (ClaimNotFound otherwise)
      - same subject_id
      - loser.status != 'superseded' (the loser must be live; merging a
        historic claim makes no editorial sense — supersede its chain
        leaf instead)
      - winner.status != 'superseded' (can't merge into a dead claim)
      - similarity, if provided, is in [-1, 1]
    """
    if not actor or not actor.strip():
        raise ClaimLifecycleError("actor must not be empty")
    if loser_id == winner_id:
        raise ClaimLifecycleError("cannot merge a claim with itself")
    if similarity is not None and not -1.0 <= similarity <= 1.0:
        raise ClaimLifecycleError(f"similarity must be in [-1, 1], got {similarity}")

    loser = _load_claim_or_raise(db, loser_id)
    winner = _load_claim_or_raise(db, winner_id)

    if loser.subject_id != winner.subject_id:
        raise ClaimLifecycleError(
            "cannot merge across subjects — loser and winner belong to "
            "different subjects"
        )
    if loser.status == "superseded":
        raise ClaimLifecycleError(
            f"claim {loser_id} is already superseded — merging a historic "
            "claim has no editorial meaning; merge the leaf of its chain"
        )
    if winner.status == "superseded":
        raise ClaimLifecycleError(
            f"winner {winner_id} is itself superseded — pick a live target"
        )

    # Set the link BEFORE the status flip so the migration-0004 CHECK
    # (status='superseded') = (superseded_by IS NOT NULL) holds in the
    # single UPDATE that _record_review emits.
    loser.superseded_by = winner.id
    payload: dict[str, Any] = {"winner_claim_id": str(winner.id)}
    if similarity is not None:
        payload["similarity"] = similarity
    if note is not None:
        payload["note"] = note

    loser, _ = _record_review(
        db,
        claim=loser,
        actor=actor,
        action="merge",
        new_status="superseded",
        payload=payload,
    )
    return loser
