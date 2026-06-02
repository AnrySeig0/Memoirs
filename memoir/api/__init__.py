"""FastAPI cho Review UI.

Editor accept / reject / edit / flag từng claim, hiển thị cạnh câu gốc
(utterance grounding). Ghi review_log audit trail. Mọi state mutation
đi qua repo function trong 1 transaction với insert review_log row —
không có path nào sửa claim mà không có audit.

Merge và supersede không nằm trong M3 (merge ↔ M5 cùng dedup candidates;
supersede ↔ M4 correction flow). `review_log.action` đã CHECK 6 giá trị
trong DB để M4/M5 ghi đè vào cùng audit table không cần migration mới.
"""
from memoir.api.app import app, create_app

__all__ = ["app", "create_app"]
