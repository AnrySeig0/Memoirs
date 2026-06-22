"""Compatibility facade for the grounded-store surface.

The models moved to `app.db.models`, the engine/session helpers to
`app.db.session`, and the provenance audit to `app.core.audit` as part of
the layered restructure. The repository still lives here (Phase 2 moves it
to `app.repositories`). This module re-exports the same names so existing
callers (`from app.store import Claim`, `insert_claim_with_sources`, …) keep
working while the migration proceeds.

Grounding rules (enforced in code + DB) — unchanged by the move:
- Insert claim without ≥1 claim_sources row → rejected
  (`insert_claim_with_sources` writes claim + claim_sources in one
  transaction; empty list → `ValueError` before touching the DB).
- utterances are INSERT-only; claim_sources append-only; review_log
  append-only — no update/delete API, plus Postgres triggers at the DB
  layer.
- Correction = supersede: new claim + `superseded_by` on the old one; old
  text is never overwritten (M4).
"""
from app.core.audit import ProvenanceResult, audit_provenance
from app.db.models import (
    EMBEDDING_DIM,
    Base,
    Claim,
    ClaimEntity,
    ClaimSource,
    Entity,
    ReviewLog,
    Session,
    Source,
    Utterance,
)
from app.db.session import get_engine, session_scope
from app.store.repository import (
    VALID_CLAIM_STATUSES,
    VALID_REVIEW_ACTIONS,
    ClaimNotFound,
    HistoryEntry,
    accept_claim,
    claim_history,
    edit_claim,
    flag_claim,
    get_or_create_entity,
    insert_claim_with_sources,
    insert_session,
    insert_source,
    insert_utterance,
    link_claim_to_entities,
    merge_claim,
    reject_claim,
    set_claim_embedding,
    supersede_claim,
)

__all__ = [
    "Base",
    "Claim",
    "ClaimEntity",
    "ClaimNotFound",
    "ClaimSource",
    "EMBEDDING_DIM",
    "Entity",
    "HistoryEntry",
    "ProvenanceResult",
    "ReviewLog",
    "Session",
    "Source",
    "Utterance",
    "VALID_CLAIM_STATUSES",
    "VALID_REVIEW_ACTIONS",
    "accept_claim",
    "audit_provenance",
    "claim_history",
    "edit_claim",
    "flag_claim",
    "get_engine",
    "get_or_create_entity",
    "insert_claim_with_sources",
    "insert_session",
    "insert_source",
    "insert_utterance",
    "link_claim_to_entities",
    "merge_claim",
    "reject_claim",
    "session_scope",
    "set_claim_embedding",
    "supersede_claim",
]
