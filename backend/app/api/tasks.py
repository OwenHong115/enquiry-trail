"""Task action routes (S7d stub; behaviour arrives in Green).

The request model mirrors the TaskAction contract and the response
reuses the detail route's Task shape. Session and CSRF are enforced
before the handler, so those failures are already observable; the
terminal actions themselves are not implemented yet.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.enquiries import TaskOut
from app.api.reviews import require_operator
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)


@router.post("/{task_id}/complete", response_model=TaskOut)
def complete_task_route(
    task_id: uuid.UUID,
    payload: TaskActionIn,
    request: Request,
    session: Session = Depends(get_session),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    require_operator(request, session, csrf_token)
    raise NotImplementedError


@router.post("/{task_id}/cancel", response_model=TaskOut)
def cancel_task_route(
    task_id: uuid.UUID,
    payload: TaskActionIn,
    request: Request,
    session: Session = Depends(get_session),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    require_operator(request, session, csrf_token)
    raise NotImplementedError
