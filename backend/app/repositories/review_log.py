"""Append-only writes to `review_log`.

Every review action records exactly one row here in the same transaction
that mutates the claim. The table is append-only (Postgres trigger blocks
UPDATE/DELETE) — an editor who changes their mind adds a new row; no row is
ever rewritten. `claim.py` calls `insert_review_log` from inside its action
functions.
"""
import uuid
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.db.models import ReviewLog

VALID_REVIEW_ACTIONS = frozenset(
    {"accept", "reject", "edit", "flag", "merge", "supersede"}
)


def insert_review_log(
    db: OrmSession,
    *,
    claim_id: uuid.UUID,
    action: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> ReviewLog:
    """Append one audit row. Caller is responsible for validating `action`
    and `actor` (claim.py does this before mutating the claim).
    """
    log = ReviewLog(claim_id=claim_id, action=action, payload=payload, actor=actor)
    db.add(log)
    db.flush()
    return log
