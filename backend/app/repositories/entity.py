"""Repository for entities and claim↔entity links (M5).

`link_claim_to_entities` validates that the claim exists (raising the same
`ClaimNotFound` as the claim repo) before attaching links.
"""
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.db.models import Claim, ClaimEntity, Entity
from app.repositories.claim import ClaimNotFound


def get_or_create_entity(
    db: OrmSession,
    *,
    subject_id: uuid.UUID,
    kind: str,
    canonical: str,
) -> Entity:
    """Idempotent on (subject_id, kind, canonical) — the unique index in
    migration 0005 makes this safe to call repeatedly.
    """
    if not kind or not canonical:
        raise ValueError("entity kind and canonical must be non-empty")
    existing = db.execute(
        select(Entity).where(
            Entity.subject_id == subject_id,
            Entity.kind == kind,
            Entity.canonical == canonical,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Entity(subject_id=subject_id, kind=kind, canonical=canonical)
    db.add(row)
    db.flush()
    return row


def link_claim_to_entities(
    db: OrmSession,
    *,
    claim_id: uuid.UUID,
    entity_ids: Sequence[uuid.UUID],
) -> int:
    """Attach entities to a claim. Idempotent: existing links are skipped.

    Returns the number of NEW links created. Useful as a smoke check —
    repeated calls with the same arguments return 0 after the first.
    """
    if not entity_ids:
        return 0
    if db.get(Claim, claim_id) is None:
        raise ClaimNotFound(f"claim {claim_id} not found")
    unique_ids = list(dict.fromkeys(entity_ids))
    existing = set(
        db.execute(
            select(ClaimEntity.entity_id).where(
                ClaimEntity.claim_id == claim_id,
                ClaimEntity.entity_id.in_(unique_ids),
            )
        ).scalars()
    )
    created = 0
    for entity_id in unique_ids:
        if entity_id in existing:
            continue
        db.add(ClaimEntity(claim_id=claim_id, entity_id=entity_id))
        created += 1
    if created:
        db.flush()
    return created
