"""Repository functions for follow-up task actions (no commits here)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.db import event_repo, tables


def get_task_for_update(
    session: Session, task_id: uuid.UUID
) -> Any | None:
    return session.execute(
        select(
            tables.follow_up_tasks.c.enquiry_id,
            tables.follow_up_tasks.c.status,
            tables.follow_up_tasks.c.version,
        )
        .where(tables.follow_up_tasks.c.id == task_id)
        .with_for_update()
    ).one_or_none()


def get_task_with_owner(
    session: Session, task_id: uuid.UUID
) -> Any | None:
    """Read one task with its owner, for shaping a Task response."""
    owner = tables.owners.alias("task_detail_owner")
    return session.execute(
        select(
            tables.follow_up_tasks.c.id,
            tables.follow_up_tasks.c.enquiry_id,
            tables.follow_up_tasks.c.kind,
            tables.follow_up_tasks.c.title,
            tables.follow_up_tasks.c.status,
            tables.follow_up_tasks.c.due_at,
            tables.follow_up_tasks.c.completed_at,
            tables.follow_up_tasks.c.cancelled_at,
            tables.follow_up_tasks.c.version,
            owner.c.id.label("owner_id"),
            owner.c.code.label("owner_code"),
            owner.c.display_name.label("owner_display_name"),
        )
        .join(owner, owner.c.id == tables.follow_up_tasks.c.owner_id)
        .where(tables.follow_up_tasks.c.id == task_id)
    ).one_or_none()


def update_task_terminal(
    session: Session,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    status: str,
    acted_at: datetime,
    reason: str,
) -> int:
    values: dict[str, Any] = {
        "status": status,
        "action_reason": reason,
        "version": tables.follow_up_tasks.c.version + 1,
    }
    if status == "completed":
        values["completed_at"] = acted_at
    else:
        values["cancelled_at"] = acted_at
    result = session.execute(
        update(tables.follow_up_tasks)
        .where(
            tables.follow_up_tasks.c.id == task_id,
            tables.follow_up_tasks.c.version == expected_version,
        )
        .values(**values)
    )
    return result.rowcount


def get_open_task_for_update(
    session: Session, enquiry_id: uuid.UUID
) -> Any | None:
    """Lock the open follow-up task for an enquiry, if one exists."""
    return session.execute(
        select(
            tables.follow_up_tasks.c.id,
            tables.follow_up_tasks.c.owner_id,
            tables.follow_up_tasks.c.version,
        )
        .where(
            tables.follow_up_tasks.c.enquiry_id == enquiry_id,
            tables.follow_up_tasks.c.status == "open",
        )
        .order_by(tables.follow_up_tasks.c.created_at)
        .limit(1)
        .with_for_update()
    ).one_or_none()


def update_task_owner(
    session: Session, task_id: uuid.UUID, *, new_owner_id: uuid.UUID
) -> int:
    """Move a locked open task to a new owner, bumping its version."""
    result = session.execute(
        update(tables.follow_up_tasks)
        .where(tables.follow_up_tasks.c.id == task_id)
        .values(
            owner_id=new_owner_id,
            version=tables.follow_up_tasks.c.version + 1,
        )
    )
    return result.rowcount


def insert_task_event(session: Session, *args: Any, **kwargs: Any) -> uuid.UUID:
    event_repo.take_enquiry_event_lock(session, kwargs["enquiry_id"])
    return _insert_task_event_locked(session, *args, **kwargs)


def _insert_task_event_locked(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    task_id: uuid.UUID,
    event_type: str,
    reason: str,
    operator_id: uuid.UUID,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type=event_type,
            severity="info",
            actor_type="operator",
            actor_id=operator_id,
            details_json={"task_id": str(task_id), "reason": reason},
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()
