# Memoir Engine — Kế hoạch kỹ thuật V1

> **Nguyên tắc nền tảng:** Sản phẩm không phải là "LLM viết văn". Sản phẩm là **bộ nhớ đáng tin có truy vết nguồn (provenance-first memory)**. Bộ sinh văn bản là phần cuối cùng và dễ thay thế nhất. V1 dừng lại ở **bộ nhớ có cấu trúc đã được review**, chưa sinh prose hoàn chỉnh.

---

## 1. Mục tiêu V1 (định nghĩa "Done")

V1 được coi là thành công khi đạt **đúng** các tiêu chí sau — không hơn:

1. Mọi câu nói của nhân vật được lưu **bất biến (append-only)** với tọa độ nguồn đầy đủ: `session_id`, `char_start`, `char_end`, `timestamp`, `speaker`.
2. Mọi **claim** (mệnh đề chuẩn hóa) đều **truy vết được về span gốc**. Không tồn tại claim mồ côi.
3. Người biên tập có giao diện **accept / reject / edit / merge / flag** từng claim, hiển thị cạnh câu gốc.
4. Cơ chế **correction = supersede, KHÔNG ghi đè**: khi nhân vật sửa lời, claim cũ được đánh dấu bị thay thế, claim mới được thêm vào, lịch sử còn nguyên và có ngày.
5. Trùng lặp ("kể một chuyện ba lần") được **phát hiện và gợi ý merge**, nhưng mọi merge phải có **người xác nhận**.

### Tiêu chí pass/fail của V1
- **Provenance test:** Lấy ngẫu nhiên 100 claim đã review → tỉ lệ truy vết đúng về nguồn = **100%**.
- **Correction test:** Tạo một correction → claim cũ vẫn tồn tại, được đánh dấu superseded, truy được "đã nói gì → sửa thành gì → khi nào".
- **Merge safety test:** Không có merge tự động nào được commit mà không qua xác nhận của người.

> V1 KHÔNG đo "chất lượng văn xuôi". Đó là việc của V2.

---

## 2. Phạm vi

### Trong phạm vi V1
- Ingestion transcript (text; audio là tùy chọn).
- Substrate bất biến + provenance.
- Extraction thành claim có grounding bắt buộc.
- Embedding + phát hiện ứng viên trùng lặp.
- Entity linking cơ bản (người, địa điểm, tổ chức, mốc thời gian).
- Giao diện review (trái tim của V1).
- Cơ chế correction / supersede.

### Cố ý để lại (Out of scope V1)
- Sinh prose / chương hoàn chỉnh.
- Tự động giải quyết mâu thuẫn (chỉ phát hiện + đẩy cho người).
- Ontology sự kiện đời người phong phú (chỉ vài entity type, để cấu trúc tự nổi lên).
- Graph database (một đời người không "graph-scale"; Postgres là đủ).
- Đa nhân vật, fine-tuning, xử lý real-time.

---

## 3. Kiến trúc tổng thể

```
[Audio/Text]
   │  Step 1: Ingestion + ASR/diarization (nếu audio)
   ▼
[Immutable Transcript Substrate]  ← append-only, không bao giờ sửa
   │  Step 3: Segmentation (giữ nguyên offset)
   ▼
[Extraction → Grounded Claims]    ← claim không có nguồn = loại/flag
   │  Step 5: Embedding
   ▼
[Dedup candidates + Entity linking] ← gợi ý, KHÔNG tự quyết
   │
   ▼
[Review UI]  ← accept/reject/edit/merge/flag, supersede
   │
   ▼
[Curated Memory Store]  ← đầu ra V1; nền cho generation ở V2
```

Mọi tầng phía sau đều **trỏ ngược về substrate**. Không gì chạm tới generation mà chưa được review.

---

## 4. Data model (load-bearing — phải đúng từ dòng đầu)

> Provenance **không thể** retrofit. Schema này là phần quan trọng nhất của toàn dự án.

```sql
-- Nguồn gốc file (audio/text) lưu ở object storage, DB chỉ tham chiếu
CREATE TABLE sources (
    id           UUID PRIMARY KEY,
    subject_id   UUID NOT NULL,
    kind         TEXT NOT NULL,          -- 'audio' | 'text'
    storage_uri  TEXT NOT NULL,          -- ví dụ s3://.../session4.wav
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phiên phỏng vấn
CREATE TABLE sessions (
    id           UUID PRIMARY KEY,
    subject_id   UUID NOT NULL,
    source_id    UUID NOT NULL REFERENCES sources(id),
    session_no   INT  NOT NULL,
    recorded_at  TIMESTAMPTZ
);

-- SUBSTRATE BẤT BIẾN: không UPDATE, không DELETE
CREATE TABLE utterances (
    id          UUID PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES sessions(id),
    speaker     TEXT NOT NULL,           -- 'subject' | 'interviewer' | ...
    text        TEXT NOT NULL,           -- verbatim
    char_start  INT  NOT NULL,           -- offset trong transcript chuẩn hóa của session
    char_end    INT  NOT NULL,
    ts_start_ms INT,                     -- nếu có audio
    ts_end_ms   INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- CLAIM: mọi claim PHẢI có nguồn, nếu không thì không được tồn tại
CREATE TABLE claims (
    id              UUID PRIMARY KEY,
    subject_id      UUID NOT NULL,
    text            TEXT NOT NULL,        -- mệnh đề chuẩn hóa, 1 ý
    claim_type      TEXT,                 -- để loose ở V1: 'event'|'fact'|'relation'|...
    confidence      REAL NOT NULL,        -- model tự đánh giá [0,1]
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- 'pending'|'accepted'|'rejected'|'edited'|'flagged'|'superseded'
    superseded_by   UUID REFERENCES claims(id),  -- correction: trỏ tới claim mới
    embedding       VECTOR(1024),         -- pgvector
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at     TIMESTAMPTZ,
    reviewed_by     TEXT
);

-- LIÊN KẾT GROUNDING: bắt buộc, đảm bảo không có claim mồ côi
CREATE TABLE claim_sources (
    claim_id        UUID NOT NULL REFERENCES claims(id),
    utterance_id    UUID NOT NULL REFERENCES utterances(id),
    PRIMARY KEY (claim_id, utterance_id)
);

-- Entity (loose, vài type)
CREATE TABLE entities (
    id          UUID PRIMARY KEY,
    subject_id  UUID NOT NULL,
    kind        TEXT NOT NULL,            -- 'person'|'place'|'org'|'date'
    canonical   TEXT NOT NULL
);

CREATE TABLE claim_entities (
    claim_id   UUID NOT NULL REFERENCES claims(id),
    entity_id  UUID NOT NULL REFERENCES entities(id),
    PRIMARY KEY (claim_id, entity_id)
);

-- Nhật ký thao tác review (audit trail)
CREATE TABLE review_log (
    id          UUID PRIMARY KEY,
    claim_id    UUID NOT NULL REFERENCES claims(id),
    action      TEXT NOT NULL,            -- 'accept'|'reject'|'edit'|'merge'|'flag'|'supersede'
    payload     JSONB,
    actor       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Quy tắc cứng (enforce ở tầng code, không chỉ tin model):**
- Insert claim mà không có ít nhất 1 dòng `claim_sources` → từ chối.
- `utterances` chỉ INSERT, không UPDATE/DELETE (có thể chốt bằng DB trigger/quyền).
- Correction tạo claim mới + set `superseded_by` trên claim cũ; **không** ghi đè text cũ.

---

## 5. Pipeline — các step xử lý & tool open-source

| Step | Việc làm | Tool open-source gợi ý | Lưu ý |
|------|----------|------------------------|-------|
| 1. Ingestion + ASR/diarization | Audio → transcript có speaker + timestamp cấp từ | **WhisperX** (Whisper + forced alignment), **pyannote.audio** (diarization) | Nếu chỉ có text thì bỏ qua, nhưng vẫn phải gán `char_start/char_end` + `speaker` |
| 2. Substrate bất biến | Ghi append-only kèm offset | **PostgreSQL**, object storage **MinIO** | Không bao giờ sửa utterance |
| 3. Segmentation | Cắt theo lượt nói + ngữ cảnh, **giữ offset** | Python tự viết; (tùy chọn) **LangChain text splitters** | Lỗi âm thầm hay gặp: chunking làm lệch offset → mất truy vết |
| 4. Extraction → claim | LLM structured output, mỗi lần 1 mệnh đề + nguồn + confidence | Model: **Qwen / Llama** qua **vLLM**; ép schema bằng **Outlines** hoặc **Instructor** | Ép schema quan trọng hơn chọn model. Thiết kế để **dưới-trích** và flag, không đoán |
| 5. Embedding & dedup candidates | Sinh embedding, tìm ứng viên trùng | **BGE-m3** / **E5** (đa ngữ) qua **sentence-transformers**, lưu **pgvector** | Không cần Pinecone/Qdrant ở V1 — giữ trong 1 DB để dễ debug |
| 6. Entity linking & gợi ý merge | Gom entity, gợi ý merge claim trùng | **spaCy** (hoặc **underthesea** cho tiếng Việt); dedup = similarity + ngưỡng + người duyệt | Merge **high-precision, người xác nhận**. Merge sai tệ hơn để trùng |
| 7. Review UI | accept/reject/edit/merge/flag, supersede | **FastAPI** + **PostgreSQL**; FE **React** (hoặc **Streamlit/Gradio** để dựng nhanh validate quy trình) | Ưu tiên tốc độ thao tác của editor hơn vẻ đẹp |
| Orchestration | Điều phối job | **Celery + Redis** hoặc **Prefect** | Không phức tạp hóa ở V1 |

### Lưu ý tiếng Việt (xuyên suốt)
- Ưu tiên model **đa ngôn ngữ** ở Step 4–6: **Qwen**, **BGE-m3**, **underthesea/spaCy** cho NLP tiếng Việt.
- **Kiểm tra offset không lệch khi xử lý Unicode có dấu** — đây là chỗ pipeline hay vỡ thầm lặng nhất. Dùng offset theo Unicode codepoint nhất quán giữa lưu trữ và hiển thị.

---

## 6. Cơ chế Correction / Supersede (Step 7, chi tiết)

Khi nhân vật ở phiên 4 nói "thực ra là năm '62, không phải '61":

1. Trích claim mới `C_new` ("chuyển đến Detroit năm 1962"), grounding vào utterance phiên 4.
2. Editor xác nhận đây là correction của `C_old` ("...năm 1961").
3. Hệ thống: set `C_old.status = 'superseded'`, `C_old.superseded_by = C_new.id`. **Không** đụng vào `C_old.text`.
4. Ghi `review_log` action `supersede`.

→ Truy vấn "lịch sử một sự kiện" trả về cả chuỗi: đã nói gì → sửa thành gì → khi nào. Drift trở nên **nhìn thấy được**, là tính năng biên tập chứ không phải bug.

---

## 7. Lộ trình build (thứ tự ưu tiên)

| Mốc | Nội dung | Tiêu chí hoàn thành | Trạng thái |
|-----|----------|---------------------|------------|
| M1 | Substrate + provenance (Step 1–2) | Transcript vào DB append-only, mọi utterance có offset/speaker | **DONE** |
| M2 | Extraction có grounding (Step 3–4) | Mọi claim có ≥1 `claim_sources`; claim không nguồn bị loại/flag | **DONE** |
| M3 | **Review UI** (Step 7) | Editor accept/reject/edit/flag được; có audit log | **DONE** |
| M4 | Correction / supersede | Pass Correction test | TODO |
| M5 | Embedding + dedup + entity (Step 5–6) | Gợi ý merge hiển thị; merge cần xác nhận; pass Merge safety test | TODO |
| M6 | Provenance test toàn hệ | 100 claim ngẫu nhiên truy vết đúng = 100% | TODO |

### M3 — đã giao những gì

- Alembic migration `0003_m3_review_log` — bảng `review_log` (§4 schema) + Postgres trigger chặn UPDATE/DELETE. Audit row không bị viết đè; reviewer có thể bất đồng với chính mình bằng cách thêm row mới, row cũ vẫn còn.
- `memoir.store.{accept,reject,edit,flag}_claim` — mỗi function trong 1 transaction: validate → update `claims` (status/reviewed_at/reviewed_by, riêng `edit` cập nhật `text`) → insert `review_log` row. `edit` lưu `previous_text` vào payload — có thể recover lời ban đầu.
- `edit` trên `superseded` claim → refuse (M4 supersede flow là path đúng để thêm narrative mới, không sửa lịch sử).
- `memoir.api` (FastAPI):
  - `GET /claims?status=pending&subject_id=…&limit&offset` — claim + grounding utterances inline, 1 round-trip đáp ứng §1 "hiển thị cạnh câu gốc".
  - `GET /claims/{id}` — chi tiết 1 claim.
  - `POST /claims/{id}/{accept,reject,edit,flag}` — body Pydantic-validated (`actor` required, `reason`/`text` tùy action), 404 khi không tồn tại, 422 khi vi phạm lifecycle.
  - `GET /claims/{id}/log` — audit history theo thời gian.
  - `GET /healthz`. Swagger UI tại `/docs` = editor surface M3.
- `main.py` re-wire: từ FastAPI hello-world → `from memoir.api import app`. Khởi động: `uvicorn main:app --host 0.0.0.0 --port 8000`.
- Tests: `test_review_repository.py` (9 cases — 4 actions, reversal grows log, edit-superseded refused, empty actor refused, DB trigger blocks UPDATE/DELETE trên review_log), `test_api_review.py` (13 cases — happy paths, 404, 422 Pydantic, 422 lifecycle, two-action reversal).

Cách thử nghiệm thủ công:
```bash
uvicorn main:app --reload
# mở http://localhost:8000/docs (Swagger), gọi POST /claims/{id}/accept với body {"actor":"alice"}
```

FE (Streamlit/React) là follow-up — Swagger đủ làm editor surface để validate quy trình.

### M2 — đã giao những gì

- Alembic migration `0002_m2_claims` — bảng `claims` + `claim_sources` (§4 schema, trừ `embedding`/entities để dành M5). CHECK `confidence BETWEEN 0 AND 1`, CHECK `status` trong tập 6 giá trị, CHECK `superseded_by <> id`. Postgres trigger chặn UPDATE/DELETE trên `claim_sources` — provenance một khi đã xác lập không được viết đè.
- `memoir.extract.ExtractedClaim` (Pydantic) — `source_utterance_ids: list[UUID] = Field(min_length=1)`, `confidence` clamp `[0,1]`. Mọi extractor (rule/LLM/future) phải đi qua schema này; output không nguồn bị reject ở validation layer.
- `memoir.store.insert_claim_with_sources` — viết claim + claim_sources trong 1 transaction; gọi với `source_utterance_ids=[]` raise `ValueError` trước khi chạm DB. Dedup trùng utterance id, validate status/confidence.
- `memoir.segment.segment_by_utterance` — Step 3 identity segmentation (1 utterance = 1 segment), giữ nguyên offset. Glue policy `segment_by_turn_window(max_chars)` là next step nhưng không thay đổi contract M2.
- `memoir.extract.RuleExtractor` — year-detector deterministic (regex `\b(?:19|20)\d{2}\b`), confidence 0.5; làm baseline + dùng cho tests không cần LLM. `LLMExtractor` stub đầy đủ docstring chỉ rõ shape Instructor/vLLM cho follow-up.
- Tests: `test_extraction.py` (7 cases — Pydantic, RuleExtractor Vietnamese, protocol structural match), `test_claim_repository.py` (6 cases — repo reject empty / confidence / status, atomic insert, dedup, trigger), `test_grounded_pipeline.py` (e2e ingest → segment → extract → store + assert orphan claims = 0).

LLM integration (Outlines/Instructor + Qwen/Llama qua vLLM) **không nằm trong M2** — acceptance là grounding contract, không phải chất lượng văn xuôi. `LLMExtractor.extract()` raise `NotImplementedError` để rõ surface; là follow-up PR độc lập.

### M1 — đã giao những gì

- Alembic migration `0001_m1_substrate` tạo `sources`, `sessions`, `utterances` (đúng §4) + Postgres trigger chặn `UPDATE`/`DELETE` trên `utterances`.
- `memoir.store` (models, engine, repository) — repository chỉ expose `insert_*` cho utterances, không có path cập nhật/xóa từ tầng code.
- `memoir.ingest.ingest_text_transcript(turns, …)` — text-only ingestion, tính `char_start`/`char_end` theo Unicode codepoint trên transcript chuẩn hóa (`"\n".join(turn.text)`). Audio + WhisperX/pyannote để lại cho mở rộng sau.
- Tests: `tests/test_offsets.py` (pure unit, chạy không cần DB) + `tests/test_append_only.py` (cần Postgres — tự skip nếu DSN unreachable).

Cách chạy DB-backed tests cục bộ:
```bash
docker compose up -d postgres
docker exec memoir-postgres psql -U memoir -c "CREATE DATABASE memoir_test"
uv run pytest
```

> Review UI (M3) đến **trước** dedup/entity (M5) — vì niềm tin biên tập được tạo ở chỗ con người thấy và sửa được, không phải ở chỗ máy "thông minh".

---

## 8. Cấu trúc repo gợi ý

```
memoir-engine/
├── README.md
├── docker-compose.yml          # postgres+pgvector, minio, redis
├── alembic/                    # migrations cho schema mục 4
├── memoir/
│   ├── ingest/                 # Step 1-2: ASR, diarization, ghi substrate
│   ├── segment/                # Step 3: chunking giữ offset
│   ├── extract/                # Step 4: LLM + schema (Outlines/Instructor)
│   ├── resolve/                # Step 5-6: embedding, dedup, entity
│   ├── store/                  # models, repository, ràng buộc grounding
│   └── api/                    # FastAPI cho Review UI
├── ui/                         # React (hoặc Streamlit prototype)
├── tests/
│   ├── test_provenance.py      # 100% truy vết
│   ├── test_correction.py      # supersede không mất dữ liệu
│   └── test_merge_safety.py    # không auto-commit merge
└── workers/                    # Celery/Prefect jobs
```

---

## 9. Rủi ro & nguyên tắc chống phức tạp sớm

- **Rủi ro lớn nhất:** một câu trả lời sai mà tự tin (auto-merge sai, tự chọn ngày "đúng") làm xói mòn niềm tin biên tập **vĩnh viễn**. → Tư thế đúng ở V1: trích ít hơn, merge ít hơn, **không tự giải quyết gì**, mọi quyết định người thấy được và đảo ngược được.
- Không dùng graph DB / vector DB ngoài ở V1 — Postgres + pgvector là đủ và dễ debug.
- Không xây ontology phong phú vội — để cấu trúc tự nổi lên từ claim loose.
- Chunking đừng làm mất offset — viết test offset ngay từ M1.

---

## 10. Đầu ra V1

Một **Curated Memory Store**: tập claim đã review, có nguồn, có lịch sử correction, có entity — sẵn sàng làm nền cho bước **grounded generation** ở V2 (mọi câu văn sinh ra sẽ trích dẫn `claim_id`, đối chiếu được từng dòng với nguồn).