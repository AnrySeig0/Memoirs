"""FastAPI app factory.

The M3 review surface — accept / reject / edit / flag — is the only
mounted router. Swagger UI at `/docs` is the M3 editor's working
interface; a Streamlit/React frontend is a follow-up that consumes the
same endpoints.
"""
from fastapi import FastAPI

from app.api.routes.claims import router as claims_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Memoir Review API",
        description=(
            "M3 surface: editor accept / reject / edit / flag claims, with "
            "an append-only audit log behind every action. See README §1 "
            "and §7."
        ),
        version="0.3.0",
    )
    app.include_router(claims_router)

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
