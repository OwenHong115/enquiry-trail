"""Health routes: liveness, and readiness that proves the database."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.api.errors import ApiError

router = APIRouter(prefix="/api/v1/health", tags=["health"])


class HealthOut(BaseModel):
    status: str


@router.get("/live", response_model=HealthOut)
def live():
    return HealthOut(status="ok")


@router.get("/ready", response_model=HealthOut)
def ready(request: Request):
    # Bounded by the engine's connect_timeout (see make_engine):
    # a dead or hung database is a 503, never an escaped exception
    # and never a wait for a default connect timeout.
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        raise ApiError(
            503, "service_unavailable", "Database is not ready"
        ) from None
    return HealthOut(status="ok")
