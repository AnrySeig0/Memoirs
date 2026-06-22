"""API v1 router aggregation.

Versioning is a code-organization boundary, not a URL prefix — the mounted
paths stay at the root (`/claims`, `/healthz`) so existing clients are
unaffected. Add a prefix here (and bump to `routes/v2/`) when the contract
changes incompatibly.
"""
from fastapi import APIRouter

from app.api.routes.v1.claims import router as claims_router
from app.api.routes.v1.health import router as health_router

v1_router = APIRouter()
v1_router.include_router(claims_router)
v1_router.include_router(health_router)

__all__ = ["v1_router"]
