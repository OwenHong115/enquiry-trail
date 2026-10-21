"""Single composition root: builds the FastAPI application."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import auth, enquiries, health, intake
from app.api.errors import ApiError, envelope_response, error_response
from app.config.routing import EXPECTED_ROUTING_SHA256, ROUTING_CONFIG
from app.db.session import make_engine, make_session_factory
from app.domain.routing import routing_config_hash
from app.services.clock import SystemBusinessClock
from app.settings import Settings

_HTTP_CODE_MAP = {
    400: "validation_failed",
    401: "unauthenticated",
    403: "csrf_invalid",
    404: "not_found",
    405: "not_found",
    409: "version_conflict",
    413: "payload_too_large",
    429: "rate_limited",
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if routing_config_hash(ROUTING_CONFIG) != EXPECTED_ROUTING_SHA256:
        raise RuntimeError(
            "routing config transcription does not match the authority hash"
        )
    app = FastAPI(title="EnquiryTrail")
    app.state.settings = settings
    app.state.routing_config = ROUTING_CONFIG
    app.state.clock = SystemBusinessClock()
    engine = make_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        field_errors = [
            {
                "path": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
            }
            for error in exc.errors()
        ]
        return envelope_response(
            request,
            422,
            "validation_failed",
            "Request validation failed",
            field_errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODE_MAP.get(
            exc.status_code,
            "internal_error" if exc.status_code >= 500 else "not_found",
        )
        return envelope_response(
            request, exc.status_code, code, "Request failed"
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        return envelope_response(
            request, 500, "internal_error", "Internal server error"
        )

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(intake.router)
    app.include_router(enquiries.router)
    return app
