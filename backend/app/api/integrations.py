"""Integration status/resume and worker health routes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated, require_operator
from app.api.errors import ApiError, ErrorEnvelope
from app.db import health_repo
from app.db.session import get_session
from app.services import health as health_service
from app.services.health import ResumeRejected

router = APIRouter(tags=["integrations"])
workers_router = APIRouter(tags=["workers"])


class IntegrationItemOut(BaseModel):
    provider: str
    status: str
    incident_id: uuid.UUID | None
    last_success_at: datetime | None
    last_error_code: str | None
    blocked_at: datetime | None
    updated_at: datetime


class IntegrationListOut(BaseModel):
    items: list[IntegrationItemOut]


class ResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_status: Literal["blocked"]
    expected_updated_at: datetime
    reason: str = Field(min_length=10, max_length=500)


class WorkerItemOut(BaseModel):
    worker_id: uuid.UUID
    started_at: datetime
    last_seen_at: datetime
    active_job_count: int
    status: Literal["alive", "stale"]


class WorkerHealthOut(BaseModel):
    workers: list[WorkerItemOut]
    stale_after_seconds: float


def _item(row) -> IntegrationItemOut:
    return IntegrationItemOut(
        provider=row.provider,
        status=row.status,
        incident_id=row.incident_id,
        last_success_at=row.last_success_at,
        last_error_code=row.last_error_code,
        blocked_at=row.blocked_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/api/v1/integrations",
    response_model=IntegrationListOut,
    responses={401: {"model": ErrorEnvelope}},
)
def list_integrations(
    request: Request, session: Session = Depends(get_session)
):
    require_authenticated(request, session)
    rows = health_repo.list_integrations(session)
    return IntegrationListOut(items=[_item(row) for row in rows])


@router.post(
    "/api/v1/integrations/{provider}/resume",
    response_model=IntegrationItemOut,
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
def resume_integration(
    provider: str,
    body: ResumeIn,
    request: Request,
    session: Session = Depends(get_session),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    resolved = require_operator(request, session, x_csrf_token)
    if provider not in health_service.KNOWN_PROVIDERS:
        raise ApiError(404, "not_found", "Unknown provider")
    # require_operator resolved the session in a read transaction;
    # release it before the service opens its own transaction.
    session.rollback()
    try:
        row = health_service.resume_provider_versioned(
            session,
            provider,
            request.app.state.crm,
            request.app.state.clock,
            operator_id=resolved.operator_id,
            reason=body.reason,
            expected_updated_at=body.expected_updated_at,
            expected_status=body.expected_status,
        )
    except ResumeRejected as rejected:
        if rejected.code == "not_found":
            raise ApiError(404, "not_found", "Integration not found") from rejected
        raise ApiError(409, rejected.code, "Resume rejected") from rejected
    return _item(row)


@workers_router.get(
    "/api/v1/health/workers",
    response_model=WorkerHealthOut,
    responses={401: {"model": ErrorEnvelope}},
)
def get_worker_health(
    request: Request, session: Session = Depends(get_session)
):
    require_authenticated(request, session)
    result = health_service.worker_health(
        session,
        heartbeat_seconds=request.app.state.settings.worker_heartbeat_seconds,
    )
    return WorkerHealthOut(**result)
