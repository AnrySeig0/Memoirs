"""FastAPI dependency wiring — `Annotated` DI aliases.

The request-scoped session comes from `app.db.session.get_db_session`
(commit on success / rollback on error). Tests override that dependency to
bind requests to the test engine. Routes use the aliases below, never raw
`Depends()`.
"""
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.services.claim import ClaimService

DBSession = Annotated[Session, Depends(get_db_session)]


def get_claim_service(db: DBSession) -> ClaimService:
    return ClaimService(db)


ClaimSvc = Annotated[ClaimService, Depends(get_claim_service)]
