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

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated, require_operator
from app.api.display import format_display_no
from app.services import read_model
from app.services.read_model import InvalidCursor, decode_cursor, encode_cursor
from app.api.enquiries import OwnerOut, TaskOut, task_out_from_row
from app.api.errors import ApiError, ErrorEnvelope
from app.db import task_repo
from app.db.session import get_session
from app.services.errors import TaskNotFound, VersionConflict
from app.services.tasks import TerminalConflict, cancel_task, complete_task

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


class TaskContactSummaryOut(BaseModel):
    id: uuid.UUID
    email: str
    first_name: str
    last_name: str | None
    phone_e164: str | None


class TaskEnquirySummaryOut(BaseModel):
    id: uuid.UUID
    display_no: str
    service_type: str
    contact: TaskContactSummaryOut


class TaskListItemOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    status: str
    due_at: datetime
    created_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    version: int
    owner: OwnerOut
    enquiry: TaskEnquirySummaryOut


class TaskListOut(BaseModel):
    items: list[TaskListItemOut]
    next_cursor: str | None


class TaskActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)


@router.get(
    "",
    response_model=TaskListOut,
    responses={401: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
)
def list_tasks_route(
    request: Request,
    session: Session = Depends(get_session),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
    status: Literal["open", "completed", "cancelled"] | None = Query(default=None),
    owner: str | None = Query(default=None),
    overdue: bool = Query(default=False),
):
    require_authenticated(request, session)
    decoded = None
    if cursor is not None:
        try:
            decoded = decode_cursor(cursor)
        except InvalidCursor as err:
            raise ApiError(
                422, "validation_failed", "Invalid cursor",
                field_errors=[{"path": "cursor", "message": "invalid cursor"}],
            ) from err
    items, next_key = read_model.list_tasks(
        session,
        limit=limit,
        cursor=decoded,
        now=request.app.state.clock.now_utc(),
        status=status,
        owner_code=owner,
        overdue=overdue,
    )
    return TaskListOut(
        items=[
            TaskListItemOut(
                id=item.row.id,
                kind=item.row.kind,
                title=item.row.title,
                status=item.row.status,
                due_at=item.row.due_at,
                created_at=item.row.created_at,
                completed_at=item.row.completed_at,
                cancelled_at=item.row.cancelled_at,
                version=item.row.version,
                owner=OwnerOut(
                    id=item.row.owner_id,
                    code=item.row.owner_code,
                    display_name=item.row.owner_display_name,
                ),
                enquiry=TaskEnquirySummaryOut(
                    id=item.row.enquiry_id,
                    display_no=format_display_no(item.row.enquiry_display_no),
                    service_type=item.row.enquiry_service_type,
                    contact=TaskContactSummaryOut(
                        id=item.row.contact_id,
                        email=item.row.email_norm,
                        first_name=item.row.first_name,
                        last_name=item.row.last_name,
                        phone_e164=item.row.phone_e164,
                    ),
                ),
            )
            for item in items
        ],
        next_cursor=encode_cursor(next_key) if next_key is not None else None,
    )


def _run_action(
    action: Callable,
    task_id: uuid.UUID,
    payload: TaskActionIn,
    request: Request,
    session: Session,
    csrf_token: str | None,
) -> TaskOut:
    resolved = require_operator(request, session, csrf_token)
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
            operator_id=resolved.operator_id,
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
