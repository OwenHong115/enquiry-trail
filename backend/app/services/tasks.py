"""Follow-up task terminal actions: complete and cancel."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db import task_repo
from app.ports.clock import BusinessClock
from app.services.errors import TaskNotFound, VersionConflict

__all__ = [
    "TaskNotFound",
    "TerminalConflict",
    "VersionConflict",
    "cancel_task",
    "complete_task",
]


class TerminalConflict(Exception):
    """The task is already in the opposite terminal state."""


def _terminal_action(
    session: Session,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    reason: str,
    clock: BusinessClock,
    operator_id: uuid.UUID,
    target_status: str,
    event_type: str,
) -> None:
    acted_at = clock.now_utc()
    with session.begin():
        task = task_repo.get_task_for_update(session, task_id)
        if task is None:
            raise TaskNotFound(f"follow-up task not found: {task_id}")
        # Status is decided before version: repeating the same
        # terminal action returns the original result with no write,
        # no event and no version check, however stale the caller's
        # expected version is.
        if task.status == target_status:
            return
        if task.status in ("completed", "cancelled"):
            raise TerminalConflict(
                f"task {task_id} is already {task.status}"
            )
        if task.version != expected_version:
            raise VersionConflict(
                f"task {task_id} is at version {task.version}, "
                f"not {expected_version}"
            )
        updated = task_repo.update_task_terminal(
            session,
            task_id,
            expected_version=expected_version,
            status=target_status,
            acted_at=acted_at,
            reason=reason,
        )
        if updated == 0:
            raise VersionConflict(
                f"task {task_id} changed under the terminal update"
            )
        task_repo.insert_task_event(
            session,
            enquiry_id=task.enquiry_id,
            task_id=task_id,
            event_type=event_type,
            reason=reason,
            operator_id=operator_id,
        )


def complete_task(
    session: Session,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    reason: str,
    clock: BusinessClock,
    operator_id: uuid.UUID,
) -> None:
    """Complete an open follow-up task."""
    _terminal_action(
        session,
        task_id,
        expected_version=expected_version,
        reason=reason,
        clock=clock,
        operator_id=operator_id,
        target_status="completed",
        event_type="task.completed",
    )


def cancel_task(
    session: Session,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    reason: str,
    clock: BusinessClock,
    operator_id: uuid.UUID,
) -> None:
    """Cancel an open follow-up task."""
    _terminal_action(
        session,
        task_id,
        expected_version=expected_version,
        reason=reason,
        clock=clock,
        operator_id=operator_id,
        target_status="cancelled",
        event_type="task.cancelled",
    )
