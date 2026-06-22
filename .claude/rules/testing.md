# Testing

Tests live in `backend/tests/` and run from `backend/`:

```bash
docker compose up -d postgres                                    # from repo root
docker exec memoir-postgres psql -U memoir -c "CREATE DATABASE memoir_test"
cd backend && uv run pytest
```

## Postgres-backed by design

Append-only invariants are enforced by plpgsql triggers, not portable to
SQLite — so the DB-dependent suite needs a real Postgres. The DSN comes from
`Settings.test_database_url` (`MEMOIR_TEST_DATABASE_URL` env / `.env`).

If the DSN is unreachable the whole DB suite **skips** (it does not fail), so
pure unit tests (offsets, embedder) still pass on a laptop without Docker.
Never make a test hard-fail on a missing DB.

## Fixtures (`tests/conftest.py`)

- `engine` (session-scoped) — runs `alembic downgrade base` + `upgrade head`
  against the test DSN; skips the suite if migrations fail.
- `db_session` — TRUNCATEs all tables RESTART IDENTITY before each test.
- `api_client` — `TestClient(app)` (from `app.main`) with `get_db` overridden
  to the test engine; handlers commit on their own sessions sharing the
  engine.

## Conventions

- Domain refusals: assert on the exception (`pytest.raises(ClaimNotFound)` /
  `pytest.raises(ValueError)` — `ClaimLifecycleError` is both a
  `ValidationError` and a `ValueError`).
- API errors: assert `status_code` and, for 422/lifecycle, the substring in
  `r.json()["detail"]`.
- Grounding is the acceptance criterion — e2e tests assert zero orphan
  claims (every claim has ≥1 `claim_sources` row).
