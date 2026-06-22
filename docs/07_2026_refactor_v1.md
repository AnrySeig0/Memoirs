# Plan: Tái cấu trúc Memoirs theo `ai_agent_template`

> Ngày: 2026-06-22 · Nhánh: `v1`
> Mục tiêu: áp dụng bố cục & quy ước phân tầng của `ai_agent_template` vào source code Memoirs.

## Quyết định phạm vi (đã chốt)

1. **Layout**: dời sang `backend/app/` mirror template 100% (mở đường cho `frontend/` + docker/k8s parity sau này).
2. **Scope**: áp dụng cả 4 mảng — tầng phân lớp cốt lõi + `core/` infra + `commands/` & `worker/` + `.claude/rules` & `docs/howto`. Bỏ qua auth/billing/multi-tenant/email/frontend/k8s (Memoirs không có nhu cầu).
3. **DB session**: giữ SQLAlchemy **sync** (rủi ro thấp, không phải viết lại mọi repo + test sang async).

## Nguyên tắc map domain

Memoirs là **pipeline app**, không phải SaaS CRUD:

- Các entity (`Source, Session, Utterance, Claim, ClaimSource, ClaimEntity, Entity, ReviewLog`) → `db/models/` + `repositories/` + `schemas/`.
- Các stage pipeline (`ingest, segment, extract, resolve`) → **thick service subpackages** dưới `services/` — đúng quy ước template "domain sở hữu infra (LLM client, embedder, parser) → subpackage", giống `services/rag/`, `services/channels/`. **Không** để chúng là top-level package.

## Bố cục đích

```
backend/
├── pyproject.toml, uv.lock, alembic.ini, alembic/   # dời từ root
└── app/
    ├── main.py                 # create_app + lifespan + register_exception_handlers + include router
    ├── core/
    │   ├── config.py           # ← memoir/config.py (giữ nguyên Settings, đã đạt chuẩn)
    │   ├── exceptions.py       # AppException base + NotFoundError/ValidationError/ConflictError
    │   ├── logging.py          # setup_logging (mới)
    │   └── audit.py            # ← memoir/store/audit.py (audit_provenance)
    ├── db/
    │   ├── base.py             # Base(DeclarativeBase) + NAMING_CONVENTION + TimestampMixin
    │   ├── session.py          # ← memoir/store/db.py (engine, session_scope, get_db_session sync)
    │   └── models/             # ← tách memoir/store/models.py thành 1 file/entity
    │       ├── __init__.py     # re-export + EMBEDDING_DIM
    │       ├── source.py · session.py · utterance.py
    │       ├── claim.py        # Claim, ClaimSource, ClaimEntity
    │       ├── entity.py · review_log.py
    ├── repositories/           # ← tách memoir/store/repository.py (653 dòng) theo entity
    │   ├── source.py           # insert_source/session/utterance
    │   ├── claim.py            # insert_claim_with_sources, accept/reject/edit/flag/merge/supersede, claim_history, set_claim_embedding
    │   ├── entity.py           # get_or_create_entity, link_claim_to_entities
    │   └── review_log.py
    ├── schemas/                # ← tách memoir/api/schemas.py (146 dòng)
    │   ├── base.py · claim.py · utterance.py · review.py
    ├── services/
    │   ├── claim.py            # ClaimService — business logic + _serialize + map exceptions (rời khỏi route)
    │   ├── pipeline.py         # orchestrate ingest→segment→extract→resolve→store
    │   ├── ingest/             # ← memoir/ingest
    │   ├── segment/            # ← memoir/segment
    │   ├── extract/            # ← memoir/extract (rule, llm, base, types — sở hữu LLM client)
    │   └── resolve/            # ← memoir/resolve (dedup, entity, embedder)
    ├── api/
    │   ├── router.py           # aggregate v1_router
    │   ├── deps.py             # ← memoir/api/deps.py, thêm Annotated aliases: DBSession, ClaimSvc
    │   ├── exception_handlers.py
    │   └── routes/v1/
    │       ├── __init__.py     # v1_router
    │       ├── claims.py       # route mỏng → gọi ClaimService
    │       └── health.py       # /healthz
    ├── worker/background/      # skeleton task nền (ingest/embed)
    └── commands/               # CLI skeleton (seed/example)
.claude/rules/                  # architecture.md, code-style.md, testing.md... (rút gọn cho Memoirs)
docs/howto/                     # add-api-endpoint.md, add-pipeline-stage.md...
```

## Các phase thực hiện

Mỗi phase phải test xanh trước khi sang bước sau.

### Phase 0 — Khung & di chuyển cơ học
- Tạo `backend/`, dời `pyproject.toml`, `uv.lock`, `alembic.ini`, `alembic/`, `tests/` vào trong; cập nhật `[tool]` paths, `pythonpath`, `packages`.
- Cập nhật `docker-compose.yml` command (`uvicorn app.main:app`) và `alembic/env.py` (`target_metadata = app.db.base.Base.metadata`).

### Phase 1 — core/ + db/
- `memoir/config.py` → `app/core/config.py` (giữ nguyên).
- Tạo `core/exceptions.py` (port AppException tree) + `core/logging.py`.
- `store/db.py` → `db/session.py`; tạo `db/base.py`; tách `store/models.py` → `db/models/<entity>.py`.
- `store/audit.py` → `core/audit.py`.

### Phase 2 — repositories/ (giữ sync, hàm stateless)
- Tách `store/repository.py` theo entity.
- Đổi `ClaimNotFound` thành subclass của `NotFoundError`; đổi các `ValueError` lifecycle ("cannot edit superseded claim") thành `ClaimLifecycleError(ValidationError)` → để handler tự map 404/422.

### Phase 3 — schemas/ + services/
- Tách `api/schemas.py` theo entity.
- Tạo `ClaimService`: chuyển `_serialize`, `_resolve_action`, gọi `find_merge_candidates` vào service. Route không còn raise `HTTPException`.
- Dời 4 stage pipeline vào `services/{ingest,segment,extract,resolve}/`; tạo `services/pipeline.py` (gom logic từ `test_grounded_pipeline`).

### Phase 4 — api/
- `api/deps.py`: thêm `DBSession`, `ClaimSvc` Annotated aliases.
- `routes/claims.py` → `routes/v1/claims.py` (mỏng); tách `health.py`; tạo `router.py` + `routes/v1/__init__.py`.
- `main.py`: `create_app()` + `register_exception_handlers()` + `include_router(api_router)`.

### Phase 5 — worker/ + commands/ + tài liệu
- Skeleton `worker/background/pipeline.py`, `commands/`.
- Viết `.claude/rules/{architecture,code-style,testing}.md` và `docs/howto/{add-api-endpoint,add-pipeline-stage}.md` bản rút gọn cho Memoirs.

### Phase 6 — Cập nhật tests
- Sửa import toàn bộ `tests/*` (`memoir.store` → `app.repositories`/`app.db`, `memoir.resolve` → `app.services.resolve`, `memoir.api` → `app.api`/`app.main`).
- Chạy `uv run pytest` đến khi xanh.

## Rủi ro / lưu ý

- **Churn import lớn**: mọi `from memoir...` đổi sang `from app...` (cả tests, alembic). Làm bằng một loạt sửa có kiểm soát + chạy test sau mỗi phase.
- **`memoir/store/__init__.py`** đang là hub re-export lớn → thay bằng các `__init__.py` theo tầng; cần rà mọi call-site.
- Giữ **sync** nên repo vẫn là hàm sync — khác chữ ký async trong rule template; chỉnh `.claude/rules/architecture.md` để ghi "sync" cho Memoirs (template note cho phép sync).
- Alembic migrations giữ nguyên file (chỉ đổi `env.py`); không cần migration mới vì schema không đổi.

## Thực thi đề xuất

Làm trong git worktree để cô lập, từng phase + chạy test, bắt đầu từ Phase 0.
