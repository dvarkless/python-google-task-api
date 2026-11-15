"""Exception hierarchy for the gtasks package."""

from __future__ import annotations

from typing import Any


class GtasksError(Exception):
    """Base class for every error raised by gtasks."""


class AuthError(GtasksError):
    """Raised when credentials are missing, invalid or rejected by the server."""


class ApiError(GtasksError):
    """Raised when the Google Tasks API answers with an error status."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        payload: dict[str, Any] | None = None,
        body: str = "",
    ) -> None:
        self.status = status
        self.message = message
        self.payload = payload
        self.body = body
        super().__init__(f"HTTP {status}: {message}")

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.message}"


class NotFoundError(ApiError):
    """Raised for HTTP 404 responses."""
