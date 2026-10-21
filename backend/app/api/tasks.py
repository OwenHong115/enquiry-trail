"""Task action routes: complete and cancel over HTTP.

The request model mirrors the TaskAction contract and the response
reuses the detail route's Task shape. The S4a task services decide
status before version; this layer only maps their exceptions and
re-reads the task (with its owner) to shape the response, including
on the repeat-terminal path.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_operator
from app.api.enquiries import TaskOut, task_out_from_row
from app.api.errors import ApiError
from app.db import task_repo
from app.db.session import get_session
from app.services.errors import TaskNotFound, VersionConflict
from app.services.tasks import TerminalConflict, cancel_task, complete_task

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)


def _run_action(
    action: Callable,
    task_id: uuid.UUID,
    payload: TaskActionIn,
    request: Request,
    session: Session,
    csrf_token: str | None,
) -> TaskOut:
    require_operator(request, session, csrf_token)
    # Auth reads opened a read transaction; end it so the service
    # can own its single write transaction.
    session.rollback()
    try:
        action(
            session,
            task_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
            clock=request.app.state.clock,
        )
    except TaskNotFound as err:
        raise ApiError(404, "not_found", "Task not found") from err
    except TerminalConflict as err:
        raise ApiError(
            409, "invalid_state", "Task is already in a terminal state"
        ) from err
    except VersionConflict as err:
        raise ApiError(
            409, "version_conflict", "Task version conflict"
        ) from err

    row = task_repo.get_task_with_owner(session, task_id)
    if row is None:  # pragma: no cover - the service just found it
        raise ApiError(404, "not_found", "Task not found")
    return task_out_from_row(row)


@router.post("/{task_id}/complete", response_model=TaskOut)
def complete_task_route(
    task_id: uuid.UUID,
    payload: TaskActionIn,
    request: Request,
    session: Session = Depends(get_session),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    return _run_action(
        complete_task, task_id, payload, request, session, csrf_token
    )


@router.post("/{task_id}/cancel", response_model=TaskOut)
def cancel_task_route(
    task_id: uuid.UUID,
    payload: TaskActionIn,
    request: Request,
    session: Session = Depends(get_session),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    return _run_action(
        cancel_task, task_id, payload, request, session, csrf_token
    )
