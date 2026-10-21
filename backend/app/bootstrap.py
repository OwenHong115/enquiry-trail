"""Single composition root: builds the FastAPI application."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request

from app.api import auth, health
from app.db.session import make_engine, make_session_factory
from app.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="EnquiryTrail Demo")
    app.state.settings = settings
    engine = make_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    app.include_router(auth.router)
    app.include_router(health.router)
    return app
