# Code style

## Repositories (`app/repositories/`)

Stateless **sync** functions, not classes:

```python
def insert_source(db: Session, *, subject_id: UUID, kind: str, storage_uri: str) -> Source:
    row = Source(subject_id=subject_id, kind=kind, storage_uri=storage_uri)
    db.add(row)
    db.flush()
    return row
```

- Keyword-only args after `db`.
- `db.flush()` / `db.refresh()`, never `db.commit()`.
- Return the ORM entity (or `None`), never IDs/dicts.
- Raise domain exceptions for not-found / invariant violations
  (`ClaimNotFound`, `ClaimLifecycleError`); plain `ValueError` is fine for
  pure substrate input validation that never reaches the API.

## Services (`app/services/`)

Class-based, hold the session:

```python
class ClaimService:
    def __init__(self, db: Session) -> None:
        self.db = db
```

- One service per domain area; call repos, never build raw queries for
  writes (reads/serialization queries are OK in the service).
- Raise domain exceptions; never return `None` to signal "not found".

## Schemas (`app/schemas/`)

- One module per entity; separate `*Request` / `*Out` models.
- `*Out` response models set `model_config = ConfigDict(from_attributes=True)`.

## Models (`app/db/models/`)

- One module per entity; import `Base` from `app/db/base.py`.
- Re-export every model from `app/db/models/__init__.py` so
  `alembic/env.py` (`target_metadata`) sees the full metadata.

## General

- Absolute imports (`from app.…`), never relative.
- Match the surrounding comment density — this codebase documents the *why*
  (grounding invariants, §-references to the README), not the *what*.
- Append-only tables (`utterances`, `claim_sources`, `review_log`) expose no
  update/delete in the repository, mirroring the Postgres triggers.
