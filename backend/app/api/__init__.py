"""FastAPI cho Review UI.

Editor accept / reject / edit / flag từng claim, hiển thị cạnh câu gốc
(utterance grounding). Ghi review_log audit trail. Mọi state mutation
đi qua repo function trong 1 transaction với insert review_log row —
không có path nào sửa claim mà không có audit.

Merge và supersede không nằm trong M3 (merge ↔ M5 cùng dedup candidates;
supersede ↔ M4 correction flow). `review_log.action` đã CHECK 6 giá trị
trong DB để M4/M5 ghi đè vào cùng audit table không cần migration mới.

The app factory now lives in `app.main` (`create_app`); the aggregated
router is assembled in `app.api.router`. Import the app via
`from app.main import app`.
"""
