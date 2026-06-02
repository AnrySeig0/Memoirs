# Postgres Schema — Memoir Engine V1

> Tham chiếu authoritative cho lớp lưu trữ. Mọi cột, FK, CHECK, index, trigger ở đây phản ánh chính xác trạng thái sau khi apply migration `0001` → `0005`. Schema này là **load-bearing** — provenance không thể retrofit (§4 README), nên file này nên được đọc trước khi bất kỳ migration mới nào được thiết kế.

**Stack:**
- PostgreSQL 16 (image `pgvector/pgvector:pg16`)
- Extension `vector` 0.8.2 cho cột `claims.embedding`
- ORM: SQLAlchemy 2.0 ([`memoir/store/models.py`](../memoir/store/models.py))
- Migrations: Alembic ([`alembic/versions/`](../alembic/versions))

**Số liệu nhanh:**
- 8 bảng nghiệp vụ + `alembic_version`
- 17 indexes (8 PK + 9 secondary, gồm 1 partial)
- 16 constraints (8 FK + 8 CHECK)
- 6 triggers (3 bảng × BEFORE UPDATE/DELETE) chặn ghi đè ở DB layer
- 3 PL/pgSQL functions thi hành append-only

---

## 1. Bird's-eye ER diagram

Toàn cảnh quan hệ giữa các bảng. Mỗi cạnh có cardinality + ghi chú vai trò.

```mermaid
erDiagram
    sources ||--o{ sessions : "owns N sessions"
    sessions ||--o{ utterances : "ordered turns"
    utterances ||--o{ claim_sources : "grounding target"
    claims ||--o{ claim_sources : "≥1 sources (M2 rule)"
    claims ||--o{ claim_entities : "tag links"
    entities ||--o{ claim_entities : "tag links"
    claims ||--o{ review_log : "audit trail"
    claims }o--|| claims : "superseded_by (self FK)"

    sources {
        UUID id PK
        UUID subject_id "logical owner"
        TEXT kind "CHECK audio|text"
        TEXT storage_uri "s3:// or file://"
        TIMESTAMPTZ created_at "default now()"
    }
    sessions {
        UUID id PK
        UUID subject_id
        UUID source_id FK
        INT session_no "UNIQUE(subject,no)"
        TIMESTAMPTZ recorded_at "nullable"
    }
    utterances {
        UUID id PK
        UUID session_id FK
        TEXT speaker
        TEXT text "verbatim"
        INT char_start "Unicode codepoint ≥0"
        INT char_end "≥ char_start"
        INT ts_start_ms "nullable for text"
        INT ts_end_ms "nullable for text"
        TIMESTAMPTZ created_at
    }
    claims {
        UUID id PK
        UUID subject_id
        TEXT text "canonical claim wording"
        TEXT claim_type "free-form §9"
        REAL confidence "CHECK 0..1"
        TEXT status "CHECK 6 values"
        UUID superseded_by FK "self-FK nullable"
        VECTOR_1024 embedding "nullable pgvector"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ reviewed_at "nullable"
        TEXT reviewed_by "nullable"
    }
    claim_sources {
        UUID claim_id PK_FK "composite PK"
        UUID utterance_id PK_FK
    }
    entities {
        UUID id PK
        UUID subject_id
        TEXT kind "free-form date|place|person|org"
        TEXT canonical "UNIQUE per (subject,kind)"
        TIMESTAMPTZ created_at
    }
    claim_entities {
        UUID claim_id PK_FK "composite PK"
        UUID entity_id PK_FK
    }
    review_log {
        UUID id PK
        UUID claim_id FK
        TEXT action "CHECK 6 values"
        JSONB payload "nullable, action-specific"
        TEXT actor
        TIMESTAMPTZ created_at
    }
```

**Đọc nhanh:**
- `||--o{` = 1-to-many (parent bắt buộc, children tùy ý có 0..N).
- `}o--||` = many-to-one trở lại — dùng cho self-FK `superseded_by`.
- `PK_FK` = cột vừa là FK vừa nằm trong composite primary key.

---

## 2. Quan hệ append-only & data-flow

Phần lớn các bảng cho phép UPDATE/DELETE bình thường — chỉ ba bảng có trigger DB chặn cứng. Sơ đồ phân loại để rõ "rủi ro ghi đè" thực sự nằm ở đâu.

```mermaid
graph TB
    subgraph Mutable["Mutable (review-driven mutations)"]
        Sources["sources<br/>file metadata"]
        Sessions["sessions<br/>session bookkeeping"]
        Claims["claims<br/>UPDATE chỉ qua repo function<br/>(luôn kèm audit row)"]
        Entities["entities<br/>(rare ops; future merge)"]
        ClaimEntities["claim_entities<br/>(tag re-labels OK)"]
    end

    subgraph AppendOnly["Append-only (trigger-enforced)"]
        Utterances["utterances<br/>SUBSTRATE<br/>§4 'không bao giờ sửa'"]
        ClaimSources["claim_sources<br/>PROVENANCE<br/>không di chuyển"]
        ReviewLog["review_log<br/>AUDIT TRAIL<br/>reversal = thêm row mới"]
    end

    Sources -.references.-> Sessions
    Sessions -.references.-> Utterances
    Utterances -.cited by.-> ClaimSources
    Claims -.references.-> ClaimSources
    Claims -.references.-> ReviewLog
    Claims -.tagged by.-> ClaimEntities
    Entities -.tagged by.-> ClaimEntities

    classDef mut fill:#0c4a6e,stroke:#0c2d49,color:#fff
    classDef ao fill:#7c2d12,stroke:#431407,color:#fff
    class Sources,Sessions,Claims,Entities,ClaimEntities mut
    class Utterances,ClaimSources,ReviewLog ao
```

**Production deploy note:** TRUNCATE bypass row-level trigger theo thiết kế Postgres. Phòng vệ phải đến từ `REVOKE TRUNCATE, UPDATE, DELETE ON utterances, claim_sources, review_log FROM <app_role>`. Conftest dev dùng superuser → TRUNCATE OK giữa tests; production thì không.

---

## 3. Table-by-table reference

### 3.1. `sources` (M1 — migration `0001_m1_substrate`)

File gốc của một phiên (audio hoặc text). DB chỉ tham chiếu — payload sống ở object storage (S3 / MinIO).

```sql
CREATE TABLE sources (
    id           UUID         PRIMARY KEY,
    subject_id   UUID         NOT NULL,
    kind         TEXT         NOT NULL,
    storage_uri  TEXT         NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT sources_kind_check CHECK (kind IN ('audio','text'))
);
```

| Cột | Type | Nullable | Default | Ghi chú |
|-----|------|----------|---------|---------|
| `id` | UUID | NO | — | PK; sinh ở Python (`uuid.uuid4`) |
| `subject_id` | UUID | NO | — | Logical owner. Không có bảng `subjects` ở V1; column lỏng. |
| `kind` | TEXT | NO | — | `'audio'` hoặc `'text'`. CHECK enforced. |
| `storage_uri` | TEXT | NO | — | e.g. `s3://memoir/session4.wav` |
| `created_at` | TIMESTAMPTZ | NO | `now()` | DB-side timestamp |

**Indexes:** `sources_pkey (id)` only.

**Mutability:** UPDATE/DELETE allowed nhưng ít khi dùng — bảng chủ yếu append.

---

### 3.2. `sessions` (M1)

Một phiên phỏng vấn. Mỗi session thuộc 1 source và mang 1 số thứ tự duy nhất trong phạm vi subject.

```sql
CREATE TABLE sessions (
    id           UUID         PRIMARY KEY,
    subject_id   UUID         NOT NULL,
    source_id    UUID         NOT NULL REFERENCES sources(id),
    session_no   INTEGER      NOT NULL,
    recorded_at  TIMESTAMPTZ
);

CREATE UNIQUE INDEX ix_sessions_subject_session_no
    ON sessions (subject_id, session_no);
```

| Cột | Type | Nullable | Default | Ghi chú |
|-----|------|----------|---------|---------|
| `id` | UUID | NO | — | PK |
| `subject_id` | UUID | NO | — | Phải khớp `sources.subject_id` (không enforce — code-layer responsibility) |
| `source_id` | UUID | NO | — | FK → `sources(id)` |
| `session_no` | INTEGER | NO | — | Phiên 1, 2, 3, … |
| `recorded_at` | TIMESTAMPTZ | YES | NULL | Lúc thu thực tế (nếu biết) |

**Indexes:**
- `sessions_pkey (id)` — PK
- `ix_sessions_subject_session_no (subject_id, session_no) UNIQUE` — chặn duplicate session number trong cùng subject

---

### 3.3. `utterances` (M1) — **append-only substrate**

Lượt nói verbatim. Đây là tầng load-bearing nhất của toàn project. Mọi claim phía dưới phải trỏ ngược về một utterance qua `claim_sources`.

```sql
CREATE TABLE utterances (
    id           UUID         PRIMARY KEY,
    session_id   UUID         NOT NULL REFERENCES sessions(id),
    speaker      TEXT         NOT NULL,
    text         TEXT         NOT NULL,
    char_start   INTEGER      NOT NULL,
    char_end     INTEGER      NOT NULL,
    ts_start_ms  INTEGER,
    ts_end_ms    INTEGER,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT utterances_char_start_nonneg CHECK (char_start >= 0),
    CONSTRAINT utterances_char_order        CHECK (char_end >= char_start)
);

CREATE INDEX ix_utterances_session
    ON utterances (session_id, char_start);
```

**Trigger function:**

```sql
CREATE FUNCTION utterances_no_modify() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'utterances is append-only: % not allowed', TG_OP
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER utterances_block_update
    BEFORE UPDATE ON utterances
    FOR EACH ROW EXECUTE FUNCTION utterances_no_modify();

CREATE TRIGGER utterances_block_delete
    BEFORE DELETE ON utterances
    FOR EACH ROW EXECUTE FUNCTION utterances_no_modify();
```

| Cột | Type | Nullable | Default | Ghi chú |
|-----|------|----------|---------|---------|
| `id` | UUID | NO | — | PK |
| `session_id` | UUID | NO | — | FK → `sessions(id)` |
| `speaker` | TEXT | NO | — | `'subject'` / `'interviewer'` / … |
| `text` | TEXT | NO | — | **Verbatim** — không normalize |
| `char_start` | INTEGER | NO | — | Offset Unicode **codepoint** (không phải byte). CHECK ≥ 0. |
| `char_end` | INTEGER | NO | — | CHECK ≥ `char_start` |
| `ts_start_ms` | INTEGER | YES | NULL | Forced-alignment ms (nếu audio); NULL cho text |
| `ts_end_ms` | INTEGER | YES | NULL | Như trên |
| `created_at` | TIMESTAMPTZ | NO | `now()` | DB-side |

**Quan trọng — offset là codepoint, không phải byte.** `len(text)` trong Python = số codepoint, khớp với Postgres TEXT semantics ở mức row. Reconstruction invariant (verified bởi `audit_provenance` §6 README):

```
transcript = '\n'.join(utterances of session ordered by char_start)
∀ u: transcript[u.char_start:u.char_end] == u.text
```

**Trigger behavior:** `UPDATE utterances SET ...` → `IntegrityError: utterances is append-only: UPDATE not allowed`. Tương tự DELETE.

**Mục tiêu:** một câu nói đã ghi vào substrate **không bao giờ thay đổi**. Drift trở thành "thêm utterance mới", không phải "sửa cái cũ".

---

### 3.4. `claims` (M2 + M4 + M5)

Mệnh đề chuẩn hóa được trích ra (hoặc thêm tay) từ substrate. Đây là đầu ra V1 chính.

```sql
CREATE TABLE claims (
    id             UUID         PRIMARY KEY,
    subject_id     UUID         NOT NULL,
    text           TEXT         NOT NULL,
    claim_type     TEXT,
    confidence     REAL         NOT NULL,
    status         TEXT         NOT NULL DEFAULT 'pending',
    superseded_by  UUID         REFERENCES claims(id),
    embedding      VECTOR(1024),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    reviewed_at    TIMESTAMPTZ,
    reviewed_by    TEXT,
    CONSTRAINT claims_confidence_range      CHECK (confidence >= 0 AND confidence <= 1),
    CONSTRAINT claims_status_check          CHECK (status IN
        ('pending','accepted','rejected','edited','flagged','superseded')),
    CONSTRAINT claims_no_self_supersede     CHECK (superseded_by IS NULL OR superseded_by <> id),
    CONSTRAINT claims_supersede_consistency CHECK
        ((status = 'superseded') = (superseded_by IS NOT NULL))
);

CREATE INDEX ix_claims_subject_status
    ON claims (subject_id, status);

CREATE INDEX ix_claims_superseded_by
    ON claims (superseded_by)
    WHERE superseded_by IS NOT NULL;
```

| Cột | Type | Nullable | Default | Migration | Ghi chú |
|-----|------|----------|---------|-----------|---------|
| `id` | UUID | NO | — | M2 | PK |
| `subject_id` | UUID | NO | — | M2 | Lỏng — không FK |
| `text` | TEXT | NO | — | M2 | Canonical wording. `edit` action thay đổi field này, lưu lại `previous_text` trong `review_log.payload`. |
| `claim_type` | TEXT | YES | NULL | M2 | §9 loose: `event` / `fact` / `relation` / `trait` / `other` hoặc bất kỳ chuỗi gì — không enforce |
| `confidence` | REAL | NO | — | M2 | CHECK `[0, 1]` |
| `status` | TEXT | NO | `'pending'` | M2 | CHECK ∈ 6 giá trị |
| `superseded_by` | UUID | YES | NULL | M2 | FK self → `claims(id)` |
| `embedding` | VECTOR(1024) | YES | NULL | M5 | pgvector. NULL cho claims chưa embed. |
| `created_at` | TIMESTAMPTZ | NO | `now()` | M2 | DB-side |
| `reviewed_at` | TIMESTAMPTZ | YES | NULL | M2 | Set bởi review action mới nhất |
| `reviewed_by` | TEXT | YES | NULL | M2 | Reviewer's identifier |

**4 CHECK constraints — invariant nào tương ứng:**

| Constraint | Invariant đảm bảo |
|------------|-------------------|
| `claims_confidence_range` | Confidence là xác suất, ∈ `[0, 1]` |
| `claims_status_check` | Lifecycle state machine không bao giờ ra ngoài 6 trạng thái |
| `claims_no_self_supersede` | Claim không thể supersede chính nó |
| `claims_supersede_consistency` | `status='superseded'` ⟺ `superseded_by IS NOT NULL`. **Ghép cặp hai cột**: code path nào set một mà thiếu cái còn lại → `IntegrityError`. (M4) |

**Indexes:**

| Index | Type | Mục đích |
|-------|------|----------|
| `claims_pkey` | UNIQUE | PK |
| `ix_claims_subject_status` | btree | `GET /claims?status=pending&subject_id=…` (Review UI queue) |
| `ix_claims_superseded_by` | btree **partial** | `WHERE superseded_by IS NOT NULL` — backward walk cho `claim_history` (M4). Partial vì đa số claim không bị superseded → index rất compact. |

**Status lifecycle:** xem [`docs/system_design.md` §3](system_design.md) — `stateDiagram-v2` chi tiết.

---

### 3.5. `claim_sources` (M2) — **append-only grounding link**

Cầu nối giữa claim và utterance gốc. Composite PK nên một cặp `(claim, utterance)` chỉ tồn tại tối đa 1 lần.

```sql
CREATE TABLE claim_sources (
    claim_id     UUID NOT NULL REFERENCES claims(id),
    utterance_id UUID NOT NULL REFERENCES utterances(id),
    PRIMARY KEY (claim_id, utterance_id)
);

CREATE INDEX ix_claim_sources_utterance
    ON claim_sources (utterance_id);
```

**Trigger function:**

```sql
CREATE FUNCTION claim_sources_no_modify() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'claim_sources is append-only: % not allowed', TG_OP
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER claim_sources_block_update
    BEFORE UPDATE ON claim_sources
    FOR EACH ROW EXECUTE FUNCTION claim_sources_no_modify();

CREATE TRIGGER claim_sources_block_delete
    BEFORE DELETE ON claim_sources
    FOR EACH ROW EXECUTE FUNCTION claim_sources_no_modify();
```

| Cột | Type | Nullable | Ghi chú |
|-----|------|----------|---------|
| `claim_id` | UUID | NO | FK → `claims(id)`, một nửa của composite PK |
| `utterance_id` | UUID | NO | FK → `utterances(id)`, nửa còn lại |

**Indexes:**
- `claim_sources_pkey (claim_id, utterance_id)` — composite PK
- `ix_claim_sources_utterance (utterance_id)` — phục vụ truy vấn ngược "claim nào dùng utterance này?" (audit trail, fact-check)

**Rule §4 — không có claim mồ côi:** một claim không có ≥1 row trong `claim_sources` thì không hợp lệ. Đây là invariant enforced ở 3 tầng (Pydantic `min_length=1` + repository `insert_claim_with_sources` raise ValueError + atomic insert trong 1 transaction). DB không enforce trực tiếp vì "≥1 row exists in a join table" không biểu diễn được bằng 1 SQL constraint duy nhất; thay vào đó, *điều đã ghi vào claim_sources không bao giờ vanish* — đó là vai trò của trigger ở đây.

---

### 3.6. `entities` (M5)

Entity canonical theo subject. Hai claim chỉ chung 1 entity nếu chung `(subject_id, kind, canonical)`.

```sql
CREATE TABLE entities (
    id          UUID         PRIMARY KEY,
    subject_id  UUID         NOT NULL,
    kind        TEXT         NOT NULL,
    canonical   TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX ix_entities_subject_kind_canonical
    ON entities (subject_id, kind, canonical);
```

| Cột | Type | Nullable | Ghi chú |
|-----|------|----------|---------|
| `id` | UUID | NO | PK |
| `subject_id` | UUID | NO | Entity-scope per subject; "Detroit" của subject A và B là 2 row khác. |
| `kind` | TEXT | NO | Free-form. Suggested vocabulary: `date`, `person`, `place`, `org`. §9 không enforce. |
| `canonical` | TEXT | NO | Dạng chuẩn hóa. e.g. `"1962"`, `"Detroit"`, `"Đà Nẵng"`. |
| `created_at` | TIMESTAMPTZ | NO | `now()` |

**Indexes:**
- `entities_pkey (id)` — PK
- `ix_entities_subject_kind_canonical (subject_id, kind, canonical) UNIQUE` — backend cho `get_or_create_entity` idempotent

---

### 3.7. `claim_entities` (M5)

Many-to-many tag giữa claim và entity.

```sql
CREATE TABLE claim_entities (
    claim_id   UUID NOT NULL REFERENCES claims(id),
    entity_id  UUID NOT NULL REFERENCES entities(id),
    PRIMARY KEY (claim_id, entity_id)
);

CREATE INDEX ix_claim_entities_entity
    ON claim_entities (entity_id);
```

| Cột | Type | Ghi chú |
|-----|------|---------|
| `claim_id` | UUID | FK + composite PK |
| `entity_id` | UUID | FK + composite PK |

**Mutability:** UPDATE/DELETE allowed — entity tags có thể relabel/retag khi editor đổi ý. Không trigger append-only ở đây (khác với `claim_sources` là provenance load-bearing).

---

### 3.8. `review_log` (M3) — **append-only audit trail**

Mọi state-mutating action trên `claims` viết ra 1 row ở đây. Reversal (accept rồi reject) tạo thêm row, không bao giờ ghi đè cái cũ.

```sql
CREATE TABLE review_log (
    id          UUID         PRIMARY KEY,
    claim_id    UUID         NOT NULL REFERENCES claims(id),
    action      TEXT         NOT NULL,
    payload     JSONB,
    actor       TEXT         NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT review_log_action_check CHECK (action IN
        ('accept','reject','edit','flag','merge','supersede'))
);

CREATE INDEX ix_review_log_claim_time
    ON review_log (claim_id, created_at);
```

**Trigger function:**

```sql
CREATE FUNCTION review_log_no_modify() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'review_log is append-only: % not allowed', TG_OP
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER review_log_block_update
    BEFORE UPDATE ON review_log
    FOR EACH ROW EXECUTE FUNCTION review_log_no_modify();

CREATE TRIGGER review_log_block_delete
    BEFORE DELETE ON review_log
    FOR EACH ROW EXECUTE FUNCTION review_log_no_modify();
```

| Cột | Type | Nullable | Default | Ghi chú |
|-----|------|----------|---------|---------|
| `id` | UUID | NO | — | PK |
| `claim_id` | UUID | NO | — | FK → `claims(id)` |
| `action` | TEXT | NO | — | CHECK ∈ 6 giá trị (M3 ship 4 + M4 supersede + M5 merge) |
| `payload` | JSONB | YES | NULL | Action-specific. Xem bảng dưới. |
| `actor` | TEXT | NO | — | Reviewer identifier |
| `created_at` | TIMESTAMPTZ | NO | `now()` | DB-side |

**Payload shape theo `action`:**

| `action` | `payload` chứa | Đặt bởi (function) |
|----------|---------------|--------------------|
| `accept` | NULL | `accept_claim` (M3) |
| `reject` | `{reason?: string}` | `reject_claim` (M3) |
| `edit` | `{previous_text: string, new_text: string}` | `edit_claim` (M3) — `previous_text` cho phép recover original wording |
| `flag` | `{reason?: string}` | `flag_claim` (M3) |
| `supersede` | `{new_claim_id: uuid, note?: string}` | `supersede_claim` (M4) |
| `merge` | `{winner_claim_id: uuid, similarity?: float, note?: string}` | `merge_claim` (M5) |

**Indexes:**
- `review_log_pkey (id)` — PK
- `ix_review_log_claim_time (claim_id, created_at)` — phục vụ `GET /claims/{id}/log` (audit history ordered by time) và `claim_history` walk

**Tại sao append-only:** §1 *"đảo ngược được"* được biểu hiện như là *"thêm row mới"*, không phải *"sửa row cũ"*. Auditor có thể tin rằng những gì đã ghi không bị thao túng — đây là ràng buộc đạo đức của hệ thống, không chỉ là kỹ thuật.

---

## 4. Constraints across tables

### 4.1. Foreign keys

```mermaid
graph LR
    sources["sources"]
    sessions["sessions"]
    utterances["utterances"]
    claims["claims"]
    claim_sources["claim_sources"]
    entities["entities"]
    claim_entities["claim_entities"]
    review_log["review_log"]

    sessions -- source_id --> sources
    utterances -- session_id --> sessions
    claim_sources -- claim_id --> claims
    claim_sources -- utterance_id --> utterances
    claim_entities -- claim_id --> claims
    claim_entities -- entity_id --> entities
    review_log -- claim_id --> claims
    claims -- "superseded_by (self)" --> claims

    classDef tbl fill:#1d4ed8,stroke:#0c2d8f,color:#fff
    class sources,sessions,utterances,claims,claim_sources,entities,claim_entities,review_log tbl
```

8 FK tổng cộng. `ON DELETE` không được khai báo → mặc định là `NO ACTION` (block xóa parent nếu có child). Trong thực tế V1 không có operational reason để xóa parent — và substrate đã được trigger chặn DELETE trực tiếp.

### 4.2. CHECK constraints

| Bảng | Constraint | Biểu thức | Vai trò |
|------|-----------|-----------|---------|
| `sources` | `sources_kind_check` | `kind IN ('audio','text')` | Đóng kín taxonomy ingest |
| `utterances` | `utterances_char_start_nonneg` | `char_start >= 0` | Offset không âm |
| `utterances` | `utterances_char_order` | `char_end >= char_start` | Span không invalid |
| `claims` | `claims_confidence_range` | `confidence BETWEEN 0 AND 1` | Xác suất hợp lệ |
| `claims` | `claims_status_check` | `status IN (6 giá trị)` | Lifecycle đóng |
| `claims` | `claims_no_self_supersede` | `superseded_by IS NULL OR superseded_by <> id` | Không vòng độ-1 |
| `claims` | `claims_supersede_consistency` | `(status='superseded') = (superseded_by IS NOT NULL)` | Ghép cặp 2 cột |
| `review_log` | `review_log_action_check` | `action IN (6 giá trị)` | Audit taxonomy đóng |

### 4.3. UNIQUE constraints

| Bảng | Index | Cột | Vai trò |
|------|-------|-----|---------|
| `sessions` | `ix_sessions_subject_session_no` | `(subject_id, session_no)` | Chặn duplicate session number trong cùng subject |
| `entities` | `ix_entities_subject_kind_canonical` | `(subject_id, kind, canonical)` | `get_or_create_entity` idempotent |
| `claim_sources` | (PK) | `(claim_id, utterance_id)` | Cùng cặp grounding không lặp |
| `claim_entities` | (PK) | `(claim_id, entity_id)` | Cùng tag link không lặp |

---

## 5. Triggers — runtime enforcement

3 cặp trigger × 3 bảng → 6 triggers, dùng 3 PL/pgSQL functions cùng pattern.

```mermaid
flowchart TD
    Client["INSERT / UPDATE / DELETE"]
    PG["Postgres"]
    UTrig{"BEFORE UPDATE<br/>BEFORE DELETE<br/>utterances?"}
    CSTrig{"BEFORE UPDATE<br/>BEFORE DELETE<br/>claim_sources?"}
    RLTrig{"BEFORE UPDATE<br/>BEFORE DELETE<br/>review_log?"}
    Raise["RAISE EXCEPTION<br/>'X is append-only: TG_OP not allowed'<br/>ERRCODE = 'check_violation'"]
    Insert["INSERT proceeds normally"]
    Commit["Row committed"]

    Client --> PG
    PG --> UTrig
    PG --> CSTrig
    PG --> RLTrig
    UTrig -- "is UPDATE/DELETE" --> Raise
    CSTrig -- "is UPDATE/DELETE" --> Raise
    RLTrig -- "is UPDATE/DELETE" --> Raise
    UTrig -- "is INSERT" --> Insert
    CSTrig -- "is INSERT" --> Insert
    RLTrig -- "is INSERT" --> Insert
    Insert --> Commit

    Raise -- "psycopg.errors.CheckViolation<br/>→ sqlalchemy.exc.IntegrityError" --> Client

    classDef refuse fill:#7c2d12,stroke:#431407,color:#fff
    classDef ok fill:#15803d,stroke:#052e16,color:#fff
    class Raise refuse
    class Insert,Commit ok
```

**Tại sao `ERRCODE = 'check_violation'`:** không có errcode → exception map sang generic `InternalError` ở SQLAlchemy. Có errcode `23514` (check_violation) → map sang `IntegrityError`, khớp với cách Pydantic và CHECK constraints khác emit lỗi → API layer xử lý đồng nhất (`IntegrityError → 422 hoặc 500 tùy context`).

**Trigger không fire trên:**
- `TRUNCATE` (Postgres design) — phòng vệ qua `REVOKE` ở production.
- `INSERT` — đúng theo thiết kế (append-only = chỉ cho phép thêm).
- Cascade DELETE từ parent — nhưng các bảng này không có ON DELETE CASCADE.

---

## 6. Migration order

Mỗi PR M-* tương ứng 1 migration. Apply tuần tự không thể skip — claims (M2) refer utterances (M1), v.v.

```mermaid
gitGraph
    commit id: "<base>"
    commit id: "0001_m1_substrate" tag: "M1"
    commit id: "0002_m2_claims" tag: "M2"
    commit id: "0003_m3_review_log" tag: "M3"
    commit id: "0004_m4_supersede" tag: "M4"
    commit id: "0005_m5_embedding_entities" tag: "M5 HEAD"
```

**Files:**
- [`0001_m1_substrate.py`](../alembic/versions/0001_m1_substrate.py) — sources, sessions, utterances + trigger
- [`0002_m2_claims.py`](../alembic/versions/0002_m2_claims.py) — claims, claim_sources + trigger
- [`0003_m3_review_log.py`](../alembic/versions/0003_m3_review_log.py) — review_log + trigger
- [`0004_m4_supersede.py`](../alembic/versions/0004_m4_supersede.py) — partial index `ix_claims_superseded_by` + CHECK `claims_supersede_consistency`
- [`0005_m5_embedding_entities.py`](../alembic/versions/0005_m5_embedding_entities.py) — `CREATE EXTENSION vector`, `claims.embedding`, entities, claim_entities

**Apply locally:**
```bash
docker compose up -d postgres
docker exec memoir-postgres psql -U memoir -d postgres \
    -c "CREATE DATABASE memoir_test"
MEMOIR_DATABASE_URL="postgresql+psycopg://memoir:memoir@localhost:5432/memoir_test" \
    uv run alembic upgrade head
```

**Verify head:**
```bash
MEMOIR_DATABASE_URL="..." uv run alembic current
# → 0005_m5_embedding_entities (head)
```

---

## 7. Truy vấn thường dùng

### 7.1. Review queue (M3)

```sql
SELECT c.id, c.text, c.confidence, c.created_at
FROM claims c
WHERE c.subject_id = $1
  AND c.status = 'pending'
ORDER BY c.created_at
LIMIT 50;
-- uses ix_claims_subject_status
```

### 7.2. Audit history một claim (M3)

```sql
SELECT id, action, payload, actor, created_at
FROM review_log
WHERE claim_id = $1
ORDER BY created_at;
-- uses ix_review_log_claim_time
```

### 7.3. Reconstruct session transcript (audit / M6)

```sql
SELECT string_agg(text, E'\n' ORDER BY char_start)
FROM utterances
WHERE session_id = $1;
-- uses ix_utterances_session
```

### 7.4. Walk supersede chain backward (M4 `claim_history`)

```sql
WITH RECURSIVE chain AS (
    SELECT id, superseded_by, 0 AS depth
    FROM claims
    WHERE id = $1
    UNION ALL
    SELECT c.id, c.superseded_by, ch.depth + 1
    FROM claims c
    JOIN chain ch ON c.id = ch.superseded_by
    WHERE ch.depth < 100
)
SELECT * FROM chain;
```

Walk-backward (predecessor lookup) dùng partial index `ix_claims_superseded_by`:

```sql
SELECT id FROM claims WHERE superseded_by = $1;
-- uses ix_claims_superseded_by (partial)
```

### 7.5. Dedup candidates (M5)

```sql
SELECT
    a.id AS a_id,
    b.id AS b_id,
    1 - (a.embedding <=> b.embedding) AS similarity
FROM claims a
JOIN claims b
  ON a.id < b.id
 AND a.subject_id = b.subject_id
WHERE a.subject_id = $1
  AND a.status <> 'superseded'
  AND b.status <> 'superseded'
  AND a.embedding IS NOT NULL
  AND b.embedding IS NOT NULL
  AND 1 - (a.embedding <=> b.embedding) >= $2
ORDER BY similarity DESC
LIMIT $3;
-- pgvector <=> operator; sequential scan ở V1 scale
```

### 7.6. Provenance audit smoke (M6)

```sql
-- Claims không có grounding rows (must be empty):
SELECT c.id FROM claims c
LEFT JOIN claim_sources cs ON cs.claim_id = c.id
WHERE cs.claim_id IS NULL;

-- Utterances được tham chiếu nhưng có offset không khớp len(text):
-- (chỉ phát hiện được ở app layer, DB không có codepoint count function)
```

---

## 8. Schema invariants — quick reference

| Invariant | Đảm bảo bởi |
|-----------|-------------|
| Utterance không bị sửa/xóa | Trigger `utterances_no_modify` |
| Claim có ≥1 grounding row | Pydantic + Repo + `claim_sources` append-only trigger |
| Grounding row không bị sửa/xóa | Trigger `claim_sources_no_modify` |
| Status ∈ 6 giá trị | CHECK `claims_status_check` |
| Confidence ∈ `[0, 1]` | CHECK `claims_confidence_range` |
| Status='superseded' ⟺ superseded_by NOT NULL | CHECK `claims_supersede_consistency` |
| Claim không tự supersede | CHECK `claims_no_self_supersede` |
| Audit row không bị sửa/xóa | Trigger `review_log_no_modify` |
| Audit action ∈ 6 giá trị | CHECK `review_log_action_check` |
| Sessions không trùng (subject, no) | UNIQUE `ix_sessions_subject_session_no` |
| Entities không trùng (subject, kind, canonical) | UNIQUE `ix_entities_subject_kind_canonical` |

---

## 9. Mở rộng tương lai (out of scope V1)

- **`subjects` table** với FK từ `sources.subject_id`, `sessions.subject_id`, `claims.subject_id`, `entities.subject_id`. Hiện cột lỏng vì V1 không cross-subject query. Khi M3 review UI cần list "all subjects with pending claims", thêm bảng này.
- **IVFFlat / HNSW index trên `claims.embedding`** cho perf khi N(claims) > ~10k. V1 sequential scan đủ.
- **Audio path:** `utterances.ts_start_ms` / `ts_end_ms` đã sẵn schema; cần wire WhisperX + pyannote ở `memoir.ingest` để populate.
- **`subjects.timezone`** để chuẩn hóa `recorded_at` và `created_at` cho display layer ở V2.
- **Soft-delete cho `sources` / `sessions`** nếu cần GDPR retraction; hiện chưa cần.
- **Materialized view cho dedup candidates** nếu live query trên `embedding <=>` quá đắt.

---

*Schema này phản ánh state sau khi apply migrations `0001` → `0005`. Khớp 1-1 với output `\dt` + `\d+ <table>` trên Postgres image `pgvector/pgvector:pg16`.*
