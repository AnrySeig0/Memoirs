"""Application exceptions.

Domain exceptions carry an HTTP status code so the layer that catches them
(`app.api.exception_handlers`, wired in Phase 4) can return a consistent
error envelope. Services raise these instead of returning ``None`` or error
codes; routes stay free of manual ``HTTPException`` plumbing.
"""
from typing import Any


class AppException(Exception):
    """Base for all application errors.

    Attributes:
        message: Human-readable error message.
        code: Machine-readable error code for clients.
        status_code: HTTP status code to return.
        details: Extra context (field names, IDs).
    """

    message: str = "An error occurred"
    code: str = "APP_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.code = code or self.__class__.code
        self.details = details or {}
        super().__init__(self.message)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, code={self.code!r})"


# === 4xx Client Errors ===


class BadRequestError(AppException):
    """Bad request (400)."""

    message = "Bad request"
    code = "BAD_REQUEST"
    status_code = 400


class NotFoundError(AppException):
    """Resource not found (404)."""

    message = "Resource not found"
    code = "NOT_FOUND"
    status_code = 404


class AlreadyExistsError(AppException):
    """Resource already exists (409)."""

    message = "Resource already exists"
    code = "ALREADY_EXISTS"
    status_code = 409


class ValidationError(AppException):
    """Validation / unprocessable entity (422).

    Used for well-formed requests that violate a domain invariant — e.g.
    editing a superseded claim.
    """

    message = "Validation error"
    code = "VALIDATION_ERROR"
    status_code = 422


# === 5xx Server Errors ===


class InternalError(AppException):
    """Internal server error (500)."""

    message = "Internal server error"
    code = "INTERNAL_ERROR"
    status_code = 500
