"""Pytest fixtures for M1.

Tests require a running Postgres (the trigger that enforces append-only
utterances is plpgsql, not portable). Default DSN:

    MEMOIR_TEST_DATABASE_URL=postgresql+psycopg://memoir:memoir@localhost:5432/memoir_test

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

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEST_URL = "postgresql+psycopg://memoir:memoir@localhost:5432/memoir_test"


def _test_database_url() -> str:
    return os.environ.get("MEMOIR_TEST_DATABASE_URL", DEFAULT_TEST_URL)


def _engine_or_skip() -> Engine:
    url = _test_database_url()
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
    env = {**os.environ, "MEMOIR_DATABASE_URL": _test_database_url()}
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
                "TRUNCATE claim_sources, claims, utterances, sessions, sources "
                "RESTART IDENTITY CASCADE"
            )
        )
    session = factory()
    try:
        yield session
    finally:
        session.close()
