"""Models, repository, ràng buộc grounding.

Quy tắc cứng (enforce ở tầng code):
- Insert claim mà không có ≥1 dòng claim_sources → từ chối.
- utterances chỉ INSERT, không UPDATE/DELETE.
- Correction = supersede: tạo claim mới + set superseded_by trên claim cũ.
  KHÔNG ghi đè text cũ.
"""
