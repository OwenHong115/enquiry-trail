"""Scenario API for the controlled failure demonstration (P6, A31)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated, require_operator
from app.api.errors import ApiError
from app.db.session import get_session
from app.demo import service as demo_service

router = APIRouter(tags=["scenarios"])

FAULT_MODES = Literal[
    "http_503_before_write", "http_401_before_write", "http_429_before_write",
    "drop_success_response", "invalid_ai_response", "crash_after_remote_write",
]


class ScenarioCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1, max_length=80)


class FaultArmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["hubspot", "slack", "ai"]
    mode: FAULT_MODES
    uses: int = Field(ge=1, le=3)
    expires_in_seconds: int = Field(ge=1, le=86400)


class FaultOut(BaseModel):
    id: uuid.UUID
    scenario_run_id: uuid.UUID
    provider: str
    mode: str
    status: str
    remaining_uses: int
    expires_at: datetime
    created_at: datetime


class ScenarioRunOut(BaseModel):
    id: uuid.UUID
    scenario_id: str
    status: str
    fault_triggered: bool
    assertion_results: list
    created_at: datetime
    finished_at: datetime | None
    faults: list[FaultOut] = []


class ScenarioListOut(BaseModel):
    items: list[ScenarioRunOut]


@router.post("/api/v1/scenarios", status_code=201)
def create_scenario(body: ScenarioCreateIn, request: Request,
                    session: Session = Depends(get_session),
                    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token")):
    require_operator(request, session, x_csrf_token)
    session.rollback()
    return demo_service.create_run(session, scenario_id=body.scenario_id)


@router.get("/api/v1/scenarios")
def list_scenarios(request: Request, session: Session = Depends(get_session)):
    require_authenticated(request, session)
    session.rollback()
    return {"items": demo_service.list_runs(session)}


@router.get("/api/v1/scenarios/{run_id}")
def get_scenario(run_id: uuid.UUID, request: Request,
                 session: Session = Depends(get_session)):
    require_authenticated(request, session)
    session.rollback()
    run = demo_service.get_run(session, run_id)
    if run is None:
        raise ApiError(404, "not_found", "Scenario run not found")
    return run


@router.post("/api/v1/scenarios/{run_id}/finish")
def finish_scenario(run_id: uuid.UUID, request: Request,
                    session: Session = Depends(get_session),
                    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token")):
    require_operator(request, session, x_csrf_token)
    session.rollback()
    run = demo_service.finish_run(session, run_id)
    if run is None:
        raise ApiError(404, "not_found", "Scenario run not found")
    return run


@router.post("/api/v1/scenarios/{run_id}/faults", status_code=201)
def arm_fault(run_id: uuid.UUID, body: FaultArmIn, request: Request,
              session: Session = Depends(get_session),
              x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token")):
    resolved = require_operator(request, session, x_csrf_token)
    session.rollback()
    fault = demo_service.arm_fault(
        session, run_id=run_id, provider=body.provider, mode=body.mode,
        uses=body.uses, expires_in_seconds=body.expires_in_seconds,
        created_by=resolved.operator_id)
    if fault is None:
        raise ApiError(404, "not_found", "Scenario run not found")
    return fault
