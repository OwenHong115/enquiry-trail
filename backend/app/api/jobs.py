"""Job routes: the operator retry action."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_operator
from app.api.errors import ApiError, ErrorEnvelope
from app.db.session import get_session
from app.services.errors import JobNotFound
from app.services.jobs import JobRetryConflict, retry_job

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


class RetryJobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_state: Literal["dead", "blocked"]
    expected_updated_at: datetime
    reason: str = Field(min_length=10, max_length=500)


class JobOut(BaseModel):
    # Exactly the eight contract keys, no others.
    id: uuid.UUID
    kind: str
    state: str
    cycle_no: int
    attempt_count: int
    cycle_attempt_count: int
    next_attempt_at: datetime | None
    updated_at: datetime


@router.post(
    "/{id}/retry",
    response_model=JobOut,
    # Schema metadata only: enforcement stays in require_operator.
    responses={
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
        409: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
def retry_job_route(
    id: uuid.UUID,
    payload: RetryJobIn,
    request: Request,
    session: Session = Depends(get_session),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    resolved = require_operator(request, session, csrf_token)
    # Auth reads opened a read transaction; end it so the service
    # can own its single write transaction.
    session.rollback()
    try:
        row = retry_job(
            session,
            id,
            expected_state=payload.expected_state,
            expected_updated_at=payload.expected_updated_at,
            reason=payload.reason,
            clock=request.app.state.clock,
            operator_id=resolved.operator_id,
        )
    except JobNotFound as err:
        raise ApiError(404, "not_found", "Job not found") from err
    except JobRetryConflict as err:
        raise ApiError(409, err.code, str(err)) from err
    return JobOut(
        id=row.id,
        kind=row.kind,
        state=row.state,
        cycle_no=row.cycle_no,
        attempt_count=row.attempt_count,
        cycle_attempt_count=row.cycle_attempt_count,
        next_attempt_at=row.next_attempt_at,
        updated_at=row.updated_at,
    )
