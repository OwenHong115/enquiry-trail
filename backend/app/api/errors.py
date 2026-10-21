"""The contract Error envelope and the API exception type."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """An error that maps onto the contract Error envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field_errors: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors or []


def error_body(
    code: str,
    message: str,
    request_id: str,
    field_errors: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or [],
            "request_id": request_id,
            "retry_after_seconds": None,
        }
    }


def error_response(request: Request, err: ApiError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=err.status_code,
        content=error_body(err.code, err.message, request_id, err.field_errors),
    )
