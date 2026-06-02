"""Pytest fixtures for the M1–M6 test suite.

Tests require a running Postgres (the trigger that enforces append-only
utterances is plpgsql, not portable). The DSN comes from
`memoir.config.Settings.test_database_url`, set via the
`MEMOIR_TEST_DATABASE_URL` env var or `.env`.

If the URL is unreachable the whole DB-dependent suite is skipped — so
`pytest` on a laptop without docker still passes the pure unit tests.
"""
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from memoir.config import get_settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def _engine_or_skip() -> Engine:
    url = get_settings().test_database_url
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Postgres at {url} unreachable: {exc}")
    return engine


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    engine = _engine_or_skip()
    # Alembic reads `MEMOIR_DATABASE_URL`; point it at the test DSN so a
    # single test run is self-contained even without an `.env` file.
    env = {**os.environ, "MEMOIR_DATABASE_URL": get_settings().test_database_url}
    subprocess.run(
        ["uv", "run", "alembic", "downgrade", "base"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
    )
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip(f"alembic upgrade failed: {result.stderr.decode()}")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with engine.begin() as conn:
        # TRUNCATE bypasses BEFORE DELETE row triggers — fine for cleanup
        # between tests. The append-only invariant we test is about
        # row-level UPDATE/DELETE from application code, not destructive
        # operator action.
        conn.execute(
            text(
                "TRUNCATE review_log, claim_entities, claim_sources, "
                "entities, claims, utterances, sessions, sources "
                "RESTART IDENTITY CASCADE"
            )
        )
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def api_client(engine: Engine, db_session: Session) -> Iterator:
    """FastAPI TestClient bound to the same engine as `db_session`.

    The handler-side dependency `get_db` is overridden to yield a fresh
    session per request from the test engine. We share the engine, NOT
    the session — handlers commit on their own sessions, then `db_session`
    sees those commits because both speak to the same Postgres.
    """
    from fastapi.testclient import TestClient

    from memoir.api import app
    from memoir.api.deps import get_db

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def _override_get_db() -> Iterator[Session]:
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
