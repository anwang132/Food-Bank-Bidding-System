"""Domain errors and their consistent JSON representation:
``{"error": {"code", "message", "details"?, "request_id"}}``."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details
        self.headers = headers


class Unauthenticated(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"


class InvalidState(AppError):
    status_code = 409
    code = "INVALID_STATE"


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_FAILED"


class TooManyRequests(AppError):
    status_code = 429
    code = "RATE_LIMITED"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
