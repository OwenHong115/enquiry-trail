"""The contract Error envelope and the API exception type."""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


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


class EnvelopeError(BaseModel):
    """The inner error object, exactly as error_body builds it."""

    code: str
    message: str
    field_errors: list[dict[str, str]] = []
    request_id: str
    retry_after_seconds: float | None = None


class ErrorEnvelope(BaseModel):
    """OpenAPI model of the contract Error envelope.

    Schema metadata only: runtime responses are still built by
    error_body/envelope_response below, never by this model.
    """

    error: EnvelopeError


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
    return envelope_response(
        request, err.status_code, err.code, err.message, err.field_errors
    )


def envelope_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    field_errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "") or str(uuid.uuid4())
    return JSONResponse(
        status_code=status_code,
        content=error_body(code, message, request_id, field_errors),
    )
