"""Models, repository, ràng buộc grounding.

Quy tắc cứng (enforce ở tầng code + DB):
- Insert claim mà không có ≥1 dòng claim_sources → từ chối.
  `insert_claim_with_sources` viết claim + claim_sources trong 1
  transaction; gọi với list rỗng → `ValueError` trước khi chạm DB.
- utterances chỉ INSERT, không UPDATE/DELETE — repository không expose
  hàm sửa/xóa và Postgres trigger từ chối ở DB layer.
- claim_sources cũng append-only — provenance một khi đã xác lập không
  được viết đè (Postgres trigger).
- review_log append-only — audit trail không bị viết đè; reviewer có thể
  bất đồng với chính mình bằng cách thêm row mới, row cũ vẫn còn.
- Correction = supersede: tạo claim mới + set superseded_by trên claim
  cũ. KHÔNG ghi đè text cũ (M4).
"""
from memoir.store.audit import ProvenanceResult, audit_provenance
from memoir.store.db import get_engine, session_scope
from memoir.store.models import (
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
from memoir.store.repository import (
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
