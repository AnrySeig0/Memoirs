"""Liveness probe — `GET /healthz`."""
from fastapi import APIRouter

router = APIRouter(tags=["meta"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
