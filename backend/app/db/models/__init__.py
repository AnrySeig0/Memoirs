"""ORM models — one module per entity.

Importing this package registers every model on `Base.metadata`, which is
what `alembic/env.py` reads as `target_metadata`. Always import models
from here (`app.db.models`) rather than the individual modules.
"""
from app.db.base import Base
from app.db.models.claim import EMBEDDING_DIM, Claim, ClaimSource
from app.db.models.entity import ClaimEntity, Entity
from app.db.models.review_log import ReviewLog
from app.db.models.session import Session
from app.db.models.source import Source
from app.db.models.utterance import Utterance

__all__ = [
    "EMBEDDING_DIM",
    "Base",
    "Claim",
    "ClaimEntity",
    "ClaimSource",
    "Entity",
    "ReviewLog",
    "Session",
    "Source",
    "Utterance",
]
