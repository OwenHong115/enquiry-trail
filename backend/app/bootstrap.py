"""Single composition root: builds the FastAPI application."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    auth, enquiries, health, intake, integrations, jobs, reviews, tasks,
)
from app.api.errors import ApiError, ErrorEnvelope, envelope_response, error_response
from app.api.ingress import IngressMiddleware
from app.config.routing import EXPECTED_ROUTING_SHA256, ROUTING_CONFIG
from app.db.session import make_engine, make_session_factory
from app.domain.routing import routing_config_hash
from app.logging import configure_logging, log_event
from app.services.clock import SystemBusinessClock
from app.settings import Settings
from app.worker.runner import _resolve_crm

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
    configure_logging()
    settings = settings or Settings.from_env()
    if routing_config_hash(ROUTING_CONFIG) != EXPECTED_ROUTING_SHA256:
        raise RuntimeError(
            "routing config transcription does not match the authority hash"
        )
    app = FastAPI(title="EnquiryTrail", responses={
        status: {"model": ErrorEnvelope}
        for status in (401, 403, 404, 409, 422, 500, 503)
    })
    app.state.settings = settings
    app.state.routing_config = ROUTING_CONFIG
    app.state.clock = SystemBusinessClock()
    # CRM port resolved once, with the worker's resolver; tests
    # may replace app.state.crm with a fake.
    app.state.crm = _resolve_crm(settings)
    engine = make_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        # Identifiers and outcome only: no headers, cookies or bodies.
        log_event(
            "http.request",
            request_id=request.state.request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        )
        return response

    app.add_middleware(IngressMiddleware, settings=settings)

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
        # This handler sits on ServerErrorMiddleware, OUTSIDE the
        # request-id middleware, so an escaping exception never
        # reaches the middleware's post-call_next lines. The error
        # path therefore owns correlation here: the same request id
        # the middleware already put on request.state (never a
        # second one), one http.request record, and one http.error
        # record carrying the exception class name only - no
        # message, args, traceback or payload. Handled errors
        # return through the middleware instead and are emitted
        # there, so no path emits twice.
        request_id = getattr(request.state, "request_id", "") or str(
            uuid.uuid4()
        )
        request.state.request_id = request_id
        log_event(
            "http.request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=500,
        )
        log_event(
            "http.error",
            request_id=request_id,
            error_code=type(exc).__name__,
        )
        response = envelope_response(
            request, 500, "internal_error", "Internal server error"
        )
        response.headers["X-Request-ID"] = request_id
        return response

    app.include_router(auth.router)
    app.include_router(health.router)
    app.include_router(intake.router)
    app.include_router(enquiries.router)
    app.include_router(reviews.router)
    app.include_router(tasks.router)
    app.include_router(jobs.router)
    app.include_router(integrations.router)
    app.include_router(integrations.workers_router)

    def build_openapi() -> dict:
        # Schema metadata only: declare the session-cookie scheme
        # the routes already enforce. Runtime auth and CSRF checks
        # stay in the session dependencies, untouched.
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, routes=app.routes
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "sessionCookie": {
                "type": "apiKey",
                "in": "cookie",
                "name": auth.COOKIE_NAME,
            }
        }
        public_paths = {"/api/v1/auth/login", "/api/v1/health/live", "/api/v1/health/ready"}
        for path, path_item in schema.get("paths", {}).items():
            if path.startswith("/api/v1/") and path not in public_paths:
                for method, operation in path_item.items():
                    if method in {"get", "post", "put", "patch", "delete"}:
                        operation["security"] = [{"sessionCookie": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = build_openapi  # type: ignore[method-assign]
    return app
