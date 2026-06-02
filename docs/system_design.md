# Memoir Engine V1 — System Design

> Bộ nhớ đáng tin có truy vết nguồn (provenance-first memory). Mọi sơ đồ dưới đây phản ánh code đã land trong M1–M6 — không phải tương lai, không phải mong muốn.

Đọc kèm: [`README.md`](../README.md) là phần lý do (§1 Nguyên tắc, §7 Roadmap, §9 Rủi ro). File này là phần như-thế-nào — cấu trúc, dòng dữ liệu, ràng buộc.

---

## 1. Pipeline tổng quan

Step 1–7 theo §3 README, ánh xạ vào module Python thực tế. Mọi tầng phía dưới đều **trỏ ngược về substrate**: không có sinh prose nào chạm tới generation trước khi qua review.

```mermaid
flowchart TD
    Input["Audio / Text input"]
    Ingest["Step 1-2: Ingest<br/>memoir.ingest"]
    Substrate[("Substrate<br/>sources · sessions · utterances<br/>APPEND-ONLY")]
    Segment["Step 3: Segment<br/>memoir.segment"]
    Extract["Step 4: Extract<br/>memoir.extract<br/>(Rule | LLM via Instructor)"]
    Embed["Step 5: Embed<br/>memoir.resolve.embedder<br/>(Deterministic | BGE-m3)"]
    Dedup["Step 6: Dedup candidates<br/>memoir.resolve.find_merge_candidates<br/>READ-ONLY"]
    EntityLink["Step 6: Entity linking<br/>memoir.resolve.entity"]
    ClaimStore[("Claims + Grounding<br/>claims · claim_sources · entities<br/>claim_sources APPEND-ONLY")]
    Review["Step 7: Review UI<br/>memoir.api FastAPI<br/>accept · reject · edit · flag<br/>supersede · merge"]
    Audit[("review_log<br/>APPEND-ONLY")]
    Curated[("Curated Memory Store<br/>= V1 output<br/>nền cho V2 generation")]
    V2["V2 Generation<br/>(out of scope)"]

    Input --> Ingest
    Ingest --> Substrate
    Substrate --> Segment
    Segment --> Extract
    Extract --> ClaimStore
    ClaimStore --> Embed
    Embed --> Dedup
    Substrate -.text.-> EntityLink
    EntityLink -.tags.-> ClaimStore
    Dedup -.candidates.-> Review
    ClaimStore --> Review
    Review --> ClaimStore
    Review --> Audit
    Audit -.read.-> Review
    ClaimStore --> Curated
    Audit --> Curated
    Curated -.handoff.-> V2

    classDef store fill:#1d4ed8,stroke:#0c2d8f,color:#fff
    classDef readonly fill:#a16207,stroke:#5e3b00,color:#fff
    classDef oos fill:#374151,stroke:#111827,color:#e5e7eb,stroke-dasharray: 5 5
    class Substrate,ClaimStore,Audit,Curated store
    class Dedup readonly
    class V2 oos
```

**Quy ước:**
- Xanh đậm = bảng dữ liệu (Postgres).
- Vàng = đường đọc-only (không bao giờ commit).
- Xám gạch nét đứt = ngoài phạm vi V1.

---

## 2. Data model (§4 schema)

ER diagram cho 8 bảng nghiệp vụ + `alembic_version`. Ghi rõ cột nào append-only, FK nào load-bearing.

```mermaid
erDiagram
    sources ||--o{ sessions : "1 source → N sessions"
    sessions ||--o{ utterances : "1 session → N utterances"
    utterances ||--o{ claim_sources : "1 utterance → N grounding rows"
    claims ||--o{ claim_sources : "1 claim → N grounding rows (≥1)"
    claims ||--o{ claim_entities : "1 claim → N entity tags"
    entities ||--o{ claim_entities : "1 entity → N claims"
    claims ||--o{ review_log : "1 claim → N audit rows"
    claims ||--o| claims : "superseded_by"

    sources {
        UUID id PK
        UUID subject_id
        TEXT kind "audio | text"
        TEXT storage_uri
        TIMESTAMPTZ created_at
    }
    sessions {
        UUID id PK
        UUID subject_id
        UUID source_id FK
        INT session_no "UNIQUE(subject,session_no)"
        TIMESTAMPTZ recorded_at
    }
    utterances {
        UUID id PK "APPEND-ONLY (trigger)"
        UUID session_id FK
        TEXT speaker
        TEXT text "verbatim"
        INT char_start "Unicode codepoint"
        INT char_end "codepoint"
        INT ts_start_ms "if audio"
        INT ts_end_ms
        TIMESTAMPTZ created_at
    }
    claims {
        UUID id PK
        UUID subject_id
        TEXT text
        TEXT claim_type "loose §9"
        REAL confidence "0..1 CHECK"
        TEXT status "6 values CHECK"
        UUID superseded_by FK "CHECK pair with status"
        VECTOR_1024 embedding "nullable"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ reviewed_at
        TEXT reviewed_by
    }
    claim_sources {
        UUID claim_id PK, FK "APPEND-ONLY (trigger)"
        UUID utterance_id PK, FK
    }
    entities {
        UUID id PK
        UUID subject_id
        TEXT kind "date|person|place|org loose"
        TEXT canonical "UNIQUE per subject+kind"
        TIMESTAMPTZ created_at
    }
    claim_entities {
        UUID claim_id PK, FK
        UUID entity_id PK, FK
    }
    review_log {
        UUID id PK "APPEND-ONLY (trigger)"
        UUID claim_id FK
        TEXT action "6 values CHECK"
        JSONB payload
        TEXT actor
        TIMESTAMPTZ created_at
    }
```

**3 bảng append-only ở tầng DB:** `utterances`, `claim_sources`, `review_log` — Postgres trigger fire `BEFORE UPDATE` và `BEFORE DELETE` ở mỗi bảng, raise `EXCEPTION ... ERRCODE='check_violation'` (map sang SQLAlchemy `IntegrityError`).

---

## 3. Vòng đời một Claim

Mỗi action điều chỉnh `claims.status` AND viết 1 row vào `review_log` trong cùng 1 transaction. Không có path nào mutate claim mà không audit.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> pending : "insert_claim_with_sources<br/>(≥1 claim_sources required)"

    pending --> accepted : "accept_claim"
    pending --> rejected : "reject_claim"
    pending --> edited : "edit_claim<br/>(payload.previous_text)"
    pending --> flagged : "flag_claim"
    pending --> superseded : "supersede_claim (M4)<br/>or merge_claim (M5)"

    accepted --> rejected : "reject_claim<br/>(reversal, log grows)"
    accepted --> edited : "edit_claim"
    accepted --> flagged : "flag_claim"
    accepted --> superseded : "supersede / merge"

    rejected --> accepted : "accept_claim (reversal)"
    rejected --> flagged : "flag_claim"
    rejected --> superseded : "supersede / merge"

    edited --> accepted : "accept_claim"
    edited --> flagged : "flag_claim"
    edited --> superseded : "supersede / merge"

    flagged --> accepted : "accept_claim"
    flagged --> rejected : "reject_claim"
    flagged --> edited : "edit_claim"
    flagged --> superseded : "supersede / merge"

    superseded --> [*] : "terminal<br/>edit/accept refused"
```

**Nguyên tắc §1 *"đảo ngược được"*:** mọi state có thể đi qua mọi state khác (trừ `superseded` là một-chiều). Mỗi transition thêm row mới vào `review_log`, không bao giờ ghi đè.

**`superseded` là terminal** vì:
- `edit_claim` refuse (M4 invariant): historic claim không được sửa text, dùng supersede chain để thêm phiên bản mới.
- `accept_claim` refuse: successor mới là claim hiện tại của narrative.

---

## 4. Append-only — phòng thủ 4 lớp

Quy tắc cứng §4 *"Insert claim mà không có ít nhất 1 dòng claim_sources → từ chối"* không phải 1 ràng buộc duy nhất — nó được defend ở mọi tầng:

```mermaid
flowchart TD
    Client[Client / Editor]
    Pydantic["Pydantic schema<br/>ExtractedClaim.source_utterance_ids<br/>min_length=1"]
    Repo["Repository<br/>insert_claim_with_sources<br/>raise ValueError if empty<br/>writes claim + sources atomically"]
    DBCheck["DB CHECK constraints<br/>confidence ∈ [0,1]<br/>status ∈ 6 values<br/>(status='superseded') = (superseded_by IS NOT NULL)"]
    DBTrigger["DB trigger<br/>utterances_no_modify<br/>claim_sources_no_modify<br/>review_log_no_modify"]
    DB[("Postgres")]

    Client -->|"untrusted input"| Pydantic
    Pydantic -->|"validated DTO"| Repo
    Repo -->|"INSERT/UPDATE"| DBCheck
    DBCheck -->|"row well-formed"| DBTrigger
    DBTrigger -->|"row append-only"| DB

    Pydantic -.->|"reject 422"| Client
    Repo -.->|"reject ValueError → 422"| Client
    DBCheck -.->|"reject CheckViolation → IntegrityError"| Client
    DBTrigger -.->|"reject 'append-only: X not allowed'"| Client

    classDef defense fill:#0c4a6e,stroke:#082f49,color:#fff
    class Pydantic,Repo,DBCheck,DBTrigger defense
```

**Triết lý:** 1 tầng có thể quên/bug; 4 tầng cùng vỡ thì coi như hệ thống không vận hành. Cost rất thấp vì mỗi tầng chỉ vài dòng code.

---

## 5. Pipeline flows — sequence diagrams

### 5.1. M1: Ingestion (text path)

```mermaid
sequenceDiagram
    actor Caller as Caller / Worker
    participant Ingest as memoir.ingest.text
    participant Repo as memoir.store.repository
    participant DB as Postgres

    Caller->>Ingest: ingest_text_transcript(subject_id, session_no, turns)
    Ingest->>Ingest: validate turns non-empty<br/>compute Unicode codepoint offsets
    Ingest->>Repo: insert_source(kind='text', storage_uri)
    Repo->>DB: INSERT sources
    Ingest->>Repo: insert_session(source_id, session_no)
    Repo->>DB: INSERT sessions
    loop for each turn
        Ingest->>Ingest: char_start = cursor<br/>char_end = char_start + len(text)<br/>cursor = char_end + sep_len
        Ingest->>Repo: insert_utterance(text, char_start, char_end)
        Repo->>Repo: assert char_end - char_start == len(text)
        Repo->>DB: INSERT utterances
    end
    Note over DB: utterances trigger BEFORE UPDATE/DELETE<br/>active — no future rewrite possible
    Ingest-->>Caller: IngestResult(source_id, session_id, utterance_ids, transcript)
```

### 5.2. M2: Grounded extraction

```mermaid
sequenceDiagram
    actor Worker
    participant Segment as memoir.segment
    participant Extract as memoir.extract<br/>(Rule | LLM)
    participant Pydantic as ExtractedClaim schema
    participant Repo as memoir.store
    participant DB as Postgres

    Worker->>Segment: segment_by_utterance(session_id)
    Segment->>DB: SELECT utterances ORDER BY char_start
    Segment-->>Worker: Iterator[Segment]<br/>(speaker, text, offsets, utterance_ids)

    loop for each segment
        Worker->>Extract: extract(segment)
        Extract->>Extract: detect year / call LLM via Instructor<br/>response_model=list[ExtractedClaim]
        Extract->>Pydantic: validate min_length(source_utterance_ids)=1
        alt source_utterance_ids empty
            Pydantic-->>Extract: ValidationError<br/>(ungrounded → reject)
        else valid
            Pydantic-->>Extract: ExtractedClaim
        end
        Extract-->>Worker: list[ExtractedClaim]

        loop for each claim
            Worker->>Repo: insert_claim_with_sources(<br/>text, source_utterance_ids, ...)
            Repo->>Repo: raise ValueError if empty (defense in depth)
            Repo->>DB: INSERT claims
            Repo->>DB: INSERT claim_sources (atomic)
        end
    end
```

### 5.3. M3: Editor review

```mermaid
sequenceDiagram
    actor Editor
    participant API as FastAPI<br/>/claims/{id}/{action}
    participant Repo as memoir.store
    participant DB as Postgres

    Editor->>API: POST /claims/{id}/edit<br/>{actor, text}
    API->>API: Pydantic validate body
    API->>Repo: edit_claim(claim_id, actor, new_text)
    Repo->>DB: SELECT claim
    alt claim missing
        Repo-->>API: ClaimNotFound
        API-->>Editor: 404
    else status='superseded'
        Repo-->>API: ValueError (historic invariant)
        API-->>Editor: 422
    else OK
        Repo->>DB: UPDATE claims SET text, status='edited', reviewed_at, reviewed_by
        Repo->>DB: INSERT review_log<br/>(action='edit', payload={previous_text, new_text})
        Note over DB: review_log trigger blocks any future<br/>UPDATE/DELETE on this row
        Repo-->>API: updated Claim
        API-->>Editor: 200 ClaimOut
    end
```

### 5.4. M4: Correction (subject self-corrects in a later session)

```mermaid
sequenceDiagram
    actor Subject
    actor Editor
    participant Ingest as memoir.ingest
    participant Extract as memoir.extract
    participant API as FastAPI
    participant Repo as memoir.store
    participant DB as Postgres

    Note over Subject: Session 3: "Năm 1961 tôi chuyển đến Detroit"
    Subject->>Ingest: ingest session 3
    Ingest->>DB: utterance_3
    Extract->>Repo: insert C_old (text='1961...', source=utt_3)
    Repo->>DB: INSERT claims, claim_sources

    Note over Subject: Session 4: "Thực ra là năm 1962"
    Subject->>Ingest: ingest session 4
    Ingest->>DB: utterance_4
    Extract->>Repo: insert C_new (text='1962...', source=utt_4)
    Repo->>DB: INSERT claims, claim_sources

    Note over Editor: Editor confirms C_new corrects C_old
    Editor->>API: POST /claims/{C_old}/supersede<br/>{actor, new_claim_id=C_new, note}
    API->>Repo: supersede_claim(old, new, actor)
    Repo->>Repo: validate 7 invariants<br/>(same subject, neither superseded, 1:1 strict)
    Repo->>DB: UPDATE C_old SET<br/>  superseded_by = C_new.id,<br/>  status = 'superseded',<br/>  reviewed_at, reviewed_by
    Note over DB: claims_supersede_consistency CHECK<br/>satisfied in single UPDATE
    Repo->>DB: INSERT review_log<br/>(action='supersede', payload={new_claim_id, note})
    Repo-->>API: updated C_old
    Note over DB: C_old.text === '1961...' UNCHANGED<br/>drift visible, not silent

    Editor->>API: GET /claims/{C_old}/history
    API->>Repo: claim_history(C_old)
    Repo->>DB: walk back (superseded_by → me) + forward (my superseded_by)
    Repo->>DB: join supersede review_log per link
    API-->>Editor: [C_old(superseded_at, by, note), C_new(leaf, nulls)]
```

### 5.5. M5: Dedup + merge

```mermaid
sequenceDiagram
    actor Worker
    actor Editor
    participant Embedder as memoir.resolve.embedder<br/>(Deterministic | BGE-m3)
    participant API as FastAPI
    participant Repo as memoir.store
    participant DB as Postgres + pgvector

    Note over Worker: 1. Embed pipeline (post-extraction job)
    Worker->>Embedder: embed(claim.text)
    Embedder-->>Worker: 1024-dim L2-normalized vector
    Worker->>Repo: set_claim_embedding(claim_id, vector)
    Repo->>DB: UPDATE claims SET embedding<br/>(refused on superseded claims)

    Note over Editor: 2. Editor opens dedup queue
    Editor->>API: GET /claims/dedup-candidates<br/>?subject_id&threshold=0.85
    API->>Repo: find_merge_candidates(subject_id, threshold)
    Repo->>DB: SELECT a.id, b.id, 1 - (a.embedding <=> b.embedding)<br/>WHERE a.id < b.id AND same subject<br/>AND both live AND both embedded
    DB-->>Repo: pairs ordered by similarity desc
    Note over DB,Repo: NO writes. §1 Merge safety:<br/>find_merge_candidates is SELECT-only
    Repo-->>API: list[MergeCandidate]
    API-->>Editor: list[MergeCandidateOut]

    Note over Editor: 3. Editor confirms a merge
    Editor->>API: POST /claims/{loser}/merge<br/>{actor, winner_claim_id, similarity}
    API->>Repo: merge_claim(loser, winner, actor, similarity)
    Repo->>Repo: validate (same subject, neither superseded,<br/>no self-merge) — 1:1 relaxed: winner CAN be<br/>target of many losers
    Repo->>DB: UPDATE claims SET<br/>  superseded_by=winner,<br/>  status='superseded'
    Repo->>DB: INSERT review_log<br/>(action='merge', payload={winner_claim_id, similarity})
    Repo-->>API: updated loser
    API-->>Editor: ClaimOut (status=superseded)

    Note over Editor: 4. Next dedup call — loser drops out
    Editor->>API: GET /claims/dedup-candidates ...
    API->>Repo: find_merge_candidates
    Note over Repo: loser excluded (status='superseded')<br/>candidate vanishes
    Repo-->>API: smaller list
```

### 5.6. M6: Provenance audit

```mermaid
sequenceDiagram
    actor Operator
    participant Audit as memoir.store.audit_provenance
    participant DB as Postgres

    Operator->>Audit: audit_provenance(claim_id)
    Audit->>DB: SELECT claim
    alt missing
        Audit-->>Operator: ok=False, issues=['claim row missing']
    else exists
        Audit->>DB: SELECT claim_sources WHERE claim_id = ?
        alt zero rows
            Audit-->>Operator: ok=False, issues=['M2 orphan']
        else ≥1 row
            loop for each source
                Audit->>DB: SELECT utterance, session, source (3 FKs)
                Audit->>Audit: check char_start≥0, end≥start,<br/>end-start == len(text)
                Audit->>DB: SELECT utterances WHERE session_id<br/>(cached per session)
                Audit->>Audit: reconstruct transcript<br/>assert transcript[start:end] == utt.text
            end
            opt status='edited'
                Audit->>DB: SELECT review_log WHERE action='edit'
                Audit->>Audit: assert payload.previous_text recoverable
            end
            opt status='superseded'
                Audit->>Audit: walk superseded_by chain<br/>detect cycle, missing successor
            end
            opt status in (accepted|rejected|edited|flagged|superseded)
                Audit->>DB: SELECT EXISTS review_log WHERE claim_id
            end
            Audit-->>Operator: ProvenanceResult(ok, issues=[])
        end
    end

    Note over Audit,DB: READ-ONLY. Audit never writes.<br/>Run on production traffic without<br/>side effects.
```

---

## 6. Module layout

Package boundary phản ánh pipeline step. Mỗi module có 1 protocol cho interface + ≥1 implementation; production-grade impl thường lazy-import heavy deps.

```mermaid
graph TB
    subgraph user["External"]
        Editor["Editor"]
        Worker["Worker / Cron"]
    end

    subgraph api["memoir.api — FastAPI"]
        AppFactory["app.py<br/>create_app"]
        Schemas["schemas.py<br/>Pydantic request/response"]
        Routes["routes/claims.py<br/>CRUD + actions + history + dedup + merge"]
        Deps["deps.py<br/>get_db (one session per request)"]
    end

    subgraph ingest["memoir.ingest"]
        IngestText["text.py<br/>ingest_text_transcript<br/>(codepoint offsets)"]
        IngestTypes["types.py<br/>Turn"]
    end

    subgraph segment["memoir.segment"]
        SegTypes["types.py<br/>Segment"]
        SegTurn["turn.py<br/>segment_by_utterance<br/>(identity policy)"]
    end

    subgraph extract["memoir.extract"]
        ExtTypes["types.py<br/>ExtractedClaim Pydantic<br/>(source_utterance_ids min_length=1)"]
        ExtBase["base.py<br/>Extractor Protocol"]
        ExtRule["rule.py<br/>RuleExtractor<br/>(year regex baseline)"]
        ExtLLM["llm.py<br/>LLMExtractor<br/>(Instructor + vLLM)"]
    end

    subgraph resolve["memoir.resolve"]
        ResTypes["types.py<br/>MergeCandidate, EntityRef"]
        ResEmbed["embedder.py<br/>Embedder Protocol<br/>Deterministic + BGEEmbedder"]
        ResDedup["dedup.py<br/>find_merge_candidates<br/>(pgvector cosine, READ-ONLY)"]
        ResEntity["entity.py<br/>EntityLinker Protocol<br/>RuleEntityLinker"]
    end

    subgraph store["memoir.store"]
        Models["models.py<br/>SQLAlchemy ORM"]
        Repo["repository.py<br/>insert_* / review actions /<br/>supersede / merge / set_embedding"]
        Audit["audit.py<br/>audit_provenance"]
        DB["db.py<br/>engine, session_scope"]
        Config["config.py<br/>Pydantic Settings"]
    end

    PG[("Postgres + pgvector")]
    MinIO[("MinIO<br/>(future audio)")]
    Redis[("Redis<br/>(future orchestration)")]

    Editor --> AppFactory
    AppFactory --> Routes
    Routes --> Schemas
    Routes --> Deps
    Routes --> Repo
    Routes --> Audit
    Routes --> ResDedup

    Worker --> IngestText
    Worker --> SegTurn
    Worker --> ExtRule
    Worker --> ExtLLM
    Worker --> ResEmbed
    Worker --> Repo

    IngestText --> Repo
    ExtRule --> ExtTypes
    ExtLLM --> ExtTypes
    SegTurn --> Models

    Repo --> Models
    Audit --> Models
    Audit -.lazy import.-> IngestText
    Models --> DB
    DB --> Config
    DB --> PG
    Repo --> PG
    Audit --> PG

    IngestText -.future audio.-> MinIO
    Worker -.future Celery.-> Redis

    classDef proto fill:#7c2d12,stroke:#431407,color:#fff
    classDef store fill:#1d4ed8,stroke:#0c2d8f,color:#fff
    classDef infra fill:#1f2937,stroke:#111827,color:#e5e7eb
    class ExtBase,ResEmbed,ResEntity proto
    class Models,Audit,Repo,DB store
    class PG,MinIO,Redis infra
```

---

## 7. API surface (M3–M5)

| Method | Path | Hành động | Body / Query |
|--------|------|-----------|--------------|
| `GET` | `/healthz` | health check | — |
| `GET` | `/claims` | list, có grounding inline | `?status=&subject_id=&limit=&offset=` |
| `GET` | `/claims/dedup-candidates` | gợi ý merge, **read-only** | `?subject_id=&threshold=&limit=` |
| `GET` | `/claims/{id}` | 1 claim + sources | — |
| `GET` | `/claims/{id}/log` | audit history | — |
| `GET` | `/claims/{id}/history` | chuỗi correction (root→leaf) | — |
| `POST` | `/claims/{id}/accept` | M3 accept | `{actor}` |
| `POST` | `/claims/{id}/reject` | M3 reject | `{actor, reason?}` |
| `POST` | `/claims/{id}/edit` | M3 edit | `{actor, text}` |
| `POST` | `/claims/{id}/flag` | M3 flag | `{actor, reason?}` |
| `POST` | `/claims/{id}/supersede` | M4 correction | `{actor, new_claim_id, note?}` |
| `POST` | `/claims/{id}/merge` | M5 merge | `{actor, winner_claim_id, similarity?, note?}` |

**Status code convention:**
- `200` — happy path; return updated claim or list.
- `404` — `ClaimNotFound` (claim/winner/successor không tồn tại).
- `422` — Pydantic body validation OR lifecycle refusal (`ValueError` từ repo: self-supersede, cross-subject, already-superseded, unknown status filter, v.v.).

**Đường ghi duy nhất:** mọi `POST` đều đi qua repository function ghi `claims` + `review_log` trong cùng transaction. Không có path nào mutate claim mà thiếu audit row.

---

## 8. §1 Acceptance — 3 hard tests

```mermaid
flowchart LR
    subgraph V1["V1 Acceptance Gate"]
        P["Provenance test<br/>100 claim ngẫu nhiên<br/>truy vết 100%"]
        C["Correction test<br/>old.text bất biến,<br/>history truy được"]
        M["Merge safety test<br/>0 auto-commit,<br/>người luôn xác nhận"]
    end

    subgraph Tests["tests/"]
        TP["test_provenance.py<br/>M6"]
        TC["test_correction.py<br/>M4"]
        TM["test_merge_safety.py<br/>M5"]
    end

    subgraph Enforcement["Defended by"]
        SchemaCheck["DB CHECK + triggers<br/>M1-M5 migrations"]
        RepoFn["Repository functions<br/>memoir.store"]
        APILayer["FastAPI Pydantic<br/>+ status code map"]
        AuditFn["audit_provenance<br/>M6"]
    end

    P --> TP
    C --> TC
    M --> TM

    TP --> AuditFn
    TC --> RepoFn
    TM --> RepoFn

    AuditFn --> SchemaCheck
    RepoFn --> SchemaCheck
    APILayer --> RepoFn

    classDef pass fill:#15803d,stroke:#052e16,color:#fff
    class P,C,M,TP,TC,TM pass
```

3/3 acceptance gate đang **PASS** trên `docker compose` Postgres. Corpus M6 test audit thực tế:

```
[M6] corpus: 158 total claims, status breakdown:
  pending=9, flagged=15, superseded=27, accepted=71, rejected=18, edited=18
```

149 reviewed claims → seeded `random.sample(100)` → `audit_provenance(c).ok` cho cả 100. Kèm full-corpus audit (149/149 OK) chứng minh không phải may rủi sample.

---

## 9. Append-only invariants — ai chặn cái gì

Bảng tham chiếu nhanh: với 1 row trong DB, ai sẽ chặn nếu code (hoặc operator) tìm cách thay đổi/xóa nó?

| Bảng | UPDATE | DELETE | TRUNCATE | Tại sao append-only |
|------|--------|--------|----------|---------------------|
| `sources` | — | — | — | (mutable; chỉ là metadata file) |
| `sessions` | — | — | — | (mutable; ít khi cập nhật) |
| **`utterances`** | trigger M1 | trigger M1 | (GRANT) | Substrate. §4 *"không bao giờ sửa"* |
| `claims` | function `_record_review` qua M3 actions | — | — | Mutation chỉ qua repo function viết audit |
| **`claim_sources`** | trigger M2 | trigger M2 | (GRANT) | Provenance một khi xác lập không di chuyển |
| `claim_entities` | — | — | — | (entity links có thể relabel) |
| `entities` | — | — | — | (canonical form có thể merge sau, không impl V1) |
| **`review_log`** | trigger M3 | trigger M3 | (GRANT) | Audit không bao giờ ghi đè; reversal = thêm row |

**Production note:** TRUNCATE bypass row-trigger theo thiết kế Postgres. Phòng vệ ở mức GRANT — production deploy nên `REVOKE TRUNCATE, UPDATE, DELETE ON utterances, claim_sources, review_log FROM <app_role>`. Conftest TRUNCATE giữa tests là quyền superuser dev DB, không phải production path.

---

## 10. Out of scope — gì ở V2

§2 README cố ý để lại. Nhắc lại ở đây vì sơ đồ trên có thể tạo ấn tượng "đã hoàn chỉnh":

- **Grounded generation** — sinh prose/chương; mọi câu phải cite `claim_id`, diff-able với verbatim source. Curated Memory Store của V1 là input cho đường này.
- **Auto-resolution of contradictions** — vẫn human-in-the-loop, V2 cũng vậy theo §9.
- **Ontology phong phú** — `claim_type` và `entities.kind` loose nhằm cho cấu trúc tự nổi lên.
- **Graph DB / external vector DB** — Postgres + pgvector đủ cho life-scale data; switch chỉ khi data scale ép buộc.
- **Multi-subject / fine-tuning / real-time** — không nằm trong V1.

---

*Cập nhật: M6 land. Mọi sơ đồ phản ánh code trên `master` sau khi merge PR #1–#7.*
