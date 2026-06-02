"""Models, repository, ràng buộc grounding.

Quy tắc cứng (enforce ở tầng code):
- Insert claim mà không có ≥1 dòng claim_sources → từ chối (M2).
- utterances chỉ INSERT, không UPDATE/DELETE — repository không expose
  hàm sửa/xóa và Postgres trigger từ chối ở DB layer.
- Correction = supersede: tạo claim mới + set superseded_by trên claim cũ.
  KHÔNG ghi đè text cũ (M4).
"""
from memoir.store.db import get_engine, session_scope
from memoir.store.models import Base, Session, Source, Utterance
from memoir.store.repository import insert_session, insert_source, insert_utterance

__all__ = [
    "Base",
    "Session",
    "Source",
    "Utterance",
    "get_engine",
    "insert_session",
    "insert_source",
    "insert_utterance",
    "session_scope",
]
