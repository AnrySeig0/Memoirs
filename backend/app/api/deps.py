"""FastAPI dependency wiring.

Tests override `get_db` to bind requests to the test engine. Production
code resolves the engine via `app.db.session.get_engine`, which reads
`MEMOIR_DATABASE_URL` from env / .env.
"""
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from app.db.session import get_engine
from app.services.claim import ClaimService


@lru_cache
def _factory() -> sessionmaker[OrmSession]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def get_db() -> Iterator[OrmSession]:
    """One request → one session. Commit on success, rollback on error.

    FastAPI executes the post-yield cleanup AFTER the response is built,
    so handlers can still mutate the session before commit and the
    response serializer sees the in-memory state.
    """
    session = _factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DBSession = Annotated[OrmSession, Depends(get_db)]


def get_claim_service(db: DBSession) -> ClaimService:
    return ClaimService(db)


ClaimSvc = Annotated[ClaimService, Depends(get_claim_service)]
