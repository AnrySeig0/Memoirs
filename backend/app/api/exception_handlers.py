"""Map domain exceptions to HTTP responses.

Registered on the app in `create_app`. Domain exceptions (`AppException`
subclasses raised by services/repositories) carry their own `status_code`
and `message`; we surface them in the same `{"detail": ...}` envelope
FastAPI's own `HTTPException` uses, so clients see one consistent shape and
routes never need manual `HTTPException` plumbing.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.exceptions import AppException

logger = logging.getLogger(__name__)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    log = logger.error if exc.status_code >= 500 else logger.warning
    log("%s: %s", exc.code, exc.message, extra={"path": request.url.path})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
