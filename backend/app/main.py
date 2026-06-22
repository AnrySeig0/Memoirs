"""FastAPI application entry point — `uvicorn app.main:app`.

Builds the app via `create_app`: configure logging, register the domain
exception handlers, mount the aggregated API router. The M3 review surface
(accept / reject / edit / flag, plus M4 supersede and M5 merge/dedup) is
served at the root; Swagger UI at `/docs` is the editor's working interface
until a dedicated frontend lands.
"""
from fastapi import FastAPI

from app.api.exception_handlers import register_exception_handlers
from app.api.router import api_router
from app.core.logging import setup_logging


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(
        title="Memoir Review API",
        description=(
            "Editor accept / reject / edit / flag claims, with an append-only "
            "audit log behind every action. See README §1 and §7."
        ),
        version="0.4.0",
    )
    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
