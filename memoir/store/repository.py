"""Insert-only repository for the M1 substrate + M2 grounded claims.

`utterances` and `claim_sources` expose no update/delete by design —
append-only is enforced both here (no API) and at the DB layer (Postgres
triggers).

The M2 contract for `insert_claim_with_sources` is the load-bearing one:
a claim without ≥1 source utterance cannot reach the DB, because the
claim row and its `claim_sources` rows are written in the same
transaction by a single function.
"""
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from memoir.store.models import Claim, ClaimSource, ReviewLog
from memoir.store.models import Session as SessionRow
from memoir.store.models import Source, Utterance

VALID_CLAIM_STATUSES = frozenset(
    {"pending", "accepted", "rejected", "edited", "flagged", "superseded"}
)

VALID_REVIEW_ACTIONS = frozenset(
    {"accept", "reject", "edit", "flag", "merge", "supersede"}
)


class ClaimNotFound(LookupError):
    """Repo signal that the caller (likely an API handler) should translate
    into a 404. Distinct from `ValueError` so the API layer can pick the
    right HTTP status.
    """


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def insert_source(
    db: OrmSession,
    *,
    subject_id: uuid.UUID,
    kind: str,
    storage_uri: str,
) -> Source:
    if kind not in {"audio", "text"}:
        raise ValueError(f"kind must be 'audio' or 'text', got {kind!r}")
    row = Source(subject_id=subject_id, kind=kind, storage_uri=storage_uri)
    db.add(row)
    db.flush()
    return row


def insert_session(
    db: OrmSession,
    *,
    subject_id: uuid.UUID,
    source_id: uuid.UUID,
    session_no: int,
    recorded_at: datetime | None = None,
) -> SessionRow:
    row = SessionRow(
        subject_id=subject_id,
        source_id=source_id,
        session_no=session_no,
        recorded_at=recorded_at,
    )
    db.add(row)
    db.flush()
    return row


def insert_utterance(
    db: OrmSession,
    *,
    session_id: uuid.UUID,
    speaker: str,
    text: str,
    char_start: int,
    char_end: int,
    ts_start_ms: int | None = None,
    ts_end_ms: int | None = None,
) -> Utterance:
    if char_start < 0 or char_end < char_start:
        raise ValueError(
            f"invalid utterance offsets: char_start={char_start}, char_end={char_end}"
        )
    if char_end - char_start != len(text):
        raise ValueError(
            "utterance offset span does not match codepoint length of text "
            f"(end-start={char_end - char_start}, len(text)={len(text)})"
        )
    row = Utterance(
        session_id=session_id,
        speaker=speaker,
        text=text,
        char_start=char_start,
        char_end=char_end,
        ts_start_ms=ts_start_ms,
        ts_end_ms=ts_end_ms,
    )
    db.add(row)
    db.flush()
    return row


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
        raise ValueError(
            "claim requires at least one source utterance (M2 grounding rule)"
        )
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0,1], got {confidence}")
    if status not in VALID_CLAIM_STATUSES:
        raise ValueError(
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
        raise ValueError(
            f"action must be one of {sorted(VALID_REVIEW_ACTIONS)}, got {action!r}"
        )
    if new_status not in VALID_CLAIM_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(VALID_CLAIM_STATUSES)}, got {new_status!r}"
        )
    if not actor or not actor.strip():
        raise ValueError("actor must not be empty")

    now = _utcnow()
    claim.status = new_status
    claim.reviewed_at = now
    claim.reviewed_by = actor
    db.flush()

    log = ReviewLog(claim_id=claim.id, action=action, payload=payload, actor=actor)
    db.add(log)
    db.flush()
    return claim, log


def accept_claim(db: OrmSession, *, claim_id: uuid.UUID, actor: str) -> Claim:
    claim = _load_claim_or_raise(db, claim_id)
    if claim.status == "superseded":
        raise ValueError(
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
        raise ValueError("new_text must not be empty")
    claim = _load_claim_or_raise(db, claim_id)
    if claim.status == "superseded":
        raise ValueError(
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
