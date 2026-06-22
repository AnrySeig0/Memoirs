# Architecture

Memoir follows the `ai_agent_template` layered architecture, adapted for a
grounded extraction pipeline. All backend Python lives in `backend/app/`.

## Layers: Routes → Services → Repositories

```
HTTP → app/api/routes/v1/ → app/services/ → app/repositories/ → app/db/
```

- **Routes never touch the DB or repositories directly.** They validate the
  request (Pydantic schema), call a service method, and return its result.
  Routes raise no `HTTPException` — domain exceptions propagate to the
  handler.
- **Services** (`app/services/`) hold business logic: orchestrate repo
  calls, serialize, raise domain exceptions. `ClaimService` is the Review
  UI's entry point.
- **Repositories** (`app/repositories/`) are stateless functions doing data
  access only — one module per entity (`source`, `claim`, `entity`,
  `review_log`).

## Thick service subpackages (the pipeline)

The extraction pipeline stages own their own infra (LLM client, embedder,
parsers), so each is a **subpackage** under `app/services/`, not a top-level
package:

- `services/ingest/` — Step 1-2: substrate write, codepoint offsets
- `services/segment/` — Step 3: utterance segmentation
- `services/extract/` — Step 4: RuleExtractor / LLMExtractor (owns LLM client)
- `services/resolve/` — Step 5-6: embedder, dedup, entity linking
- `services/pipeline.py` — orchestrates ingest → segment → extract → store

Top-level `app/` is reserved for framework concerns: `api/`, `core/`, `db/`,
`repositories/`, `schemas/`, `services/`, `worker/`, `commands/`. No new
top-level domain packages.

## Persistence

- **Sync** SQLAlchemy. Sessions come from `app/db/session.py`
  (`get_db_session` for FastAPI DI, `session_scope` for workers/CLI).
- Models live one-per-entity in `app/db/models/`; `Base` + naming
  convention in `app/db/base.py`.
- Repositories use `db.flush()` + `db.refresh()`, **never** `db.commit()` —
  the session dependency / `session_scope` owns the transaction boundary.

## Exceptions

- Domain exceptions live in `app/core/exceptions.py` (`AppException` tree
  with `status_code`).
- Claim-domain exceptions in `app/repositories/claim.py`:
  `ClaimNotFound` (→ 404) subclasses `NotFoundError`; `ClaimLifecycleError`
  (→ 422) subclasses `ValidationError` **and** `ValueError`.
- `app/api/exception_handlers.py` maps any `AppException` to
  `{"detail": message}` with its status code. Register via `create_app`.

## Dependency injection

Use `Annotated` aliases from `app/api/deps.py` — never raw `Depends()` in
route signatures:

```python
DBSession = Annotated[Session, Depends(get_db)]
ClaimSvc = Annotated[ClaimService, Depends(get_claim_service)]
```

## Entry point

`app/main.py` `create_app()` wires logging + exception handlers + the
aggregated `api_router`. `backend/main.py` is a thin shim, so both
`uvicorn main:app` (from `backend/`) and `uvicorn app.main:app` work.
