"""Repository functions for Slack notifications (no commits here)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import event_repo, tables

NOTIFICATION_KIND = "enquiry_ready"


def operation_key_for(enquiry_id: uuid.UUID) -> str:
    return f"slack:notify_ready:{enquiry_id}"


def get_notification(session: Session, enquiry_id: uuid.UUID) -> Any | None:
    return session.execute(
        select(
            tables.notifications.c.id,
            tables.notifications.c.enquiry_id,
            tables.notifications.c.kind,
            tables.notifications.c.status,
            tables.notifications.c.operation_key,
            tables.notifications.c.last_attempt_at,
            tables.notifications.c.last_error_code,
        )
        .where(
            tables.notifications.c.enquiry_id == enquiry_id,
            tables.notifications.c.kind == NOTIFICATION_KIND,
        )
        .order_by(tables.notifications.c.id)
        .limit(1)
    ).one_or_none()


def get_notification_by_operation_key(
    session: Session, operation_key: str
) -> Any | None:
    return session.execute(
        select(
            tables.notifications.c.id,
            tables.notifications.c.enquiry_id,
            tables.notifications.c.kind,
            tables.notifications.c.status,
            tables.notifications.c.operation_key,
            tables.notifications.c.last_attempt_at,
            tables.notifications.c.last_error_code,
        ).where(tables.notifications.c.operation_key == operation_key)
    ).one_or_none()


def ensure_notification(
    session: Session, enquiry_id: uuid.UUID
) -> Any:
    """Insert the pending notification row once, then return it."""
    operation_key = operation_key_for(enquiry_id)
    stmt = (
        pg_insert(tables.notifications)
        .values(
            enquiry_id=enquiry_id,
            kind=NOTIFICATION_KIND,
            status="pending",
            operation_key=operation_key,
        )
        .on_conflict_do_nothing(index_elements=["operation_key"])
    )
    session.execute(stmt)
    row = get_notification_by_operation_key(session, operation_key)
    assert row is not None
    return row


def enqueue_notify_job(
    session: Session, *, enquiry_id: uuid.UUID, next_attempt_at: datetime
) -> None:
    """Insert the notify_ready job once (existing jobs machinery)."""
    stmt = (
        pg_insert(tables.jobs)
        .values(
            kind="notify_ready",
            entity_id=enquiry_id,
            dedupe_key=f"notify_ready:{enquiry_id}",
            state="pending",
            next_attempt_at=next_attempt_at,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    session.execute(stmt)


def is_enquiry_ready(session: Session, enquiry_id: uuid.UUID) -> bool:
    row = session.execute(
        select(
            tables.enquiries.c.local_prepared_at,
            tables.crm_links.c.sync_status,
            tables.crm_links.c.synced_contact_version,
            tables.enquiries.c.required_contact_version,
        )
        .join(
            tables.crm_links,
            (tables.crm_links.c.contact_id == tables.enquiries.c.contact_id)
            & (tables.crm_links.c.provider == "hubspot"),
        )
        .where(tables.enquiries.c.id == enquiry_id)
    ).one_or_none()
    if row is None:
        return False
    return (
        row.local_prepared_at is not None
        and row.sync_status == "synced"
        and row.synced_contact_version is not None
        and row.synced_contact_version >= row.required_contact_version
    )


def get_enquiry_display(session: Session, enquiry_id: uuid.UUID) -> Any | None:
    return session.execute(
        select(
            tables.enquiries.c.display_no,
            tables.enquiries.c.service_type,
        ).where(tables.enquiries.c.id == enquiry_id)
    ).one_or_none()


def mark_sent(session: Session, notification_id: uuid.UUID) -> None:
    session.execute(
        update(tables.notifications)
        .where(tables.notifications.c.id == notification_id)
        .values(status="sent", last_error_code=None, last_attempt_at=text("clock_timestamp()"))
    )


def mark_delivery_unknown(
    session: Session, notification_id: uuid.UUID, error_code: str
) -> None:
    session.execute(
        update(tables.notifications)
        .where(tables.notifications.c.id == notification_id)
        .values(status="delivery_unknown", last_error_code=error_code, last_attempt_at=text("clock_timestamp()"))
    )


def mark_failed(
    session: Session, notification_id: uuid.UUID, error_code: str
) -> None:
    session.execute(
        update(tables.notifications)
        .where(tables.notifications.c.id == notification_id)
        .values(status="failed", last_error_code=error_code, last_attempt_at=text("clock_timestamp()"))
    )


def record_attempt_error(
    session: Session, notification_id: uuid.UUID, error_code: str
) -> None:
    session.execute(
        update(tables.notifications)
        .where(tables.notifications.c.id == notification_id)
        .values(last_error_code=error_code, last_attempt_at=text("clock_timestamp()"))
    )


def _insert_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    job_id: uuid.UUID | None,
    event_type: str,
    severity: str,
    details: dict,
) -> uuid.UUID:
    event_repo.take_enquiry_event_lock(session, enquiry_id)
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            job_id=job_id,
            event_type=event_type,
            severity=severity,
            actor_type="worker",
            details_json=details,
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def insert_sent_event(
    session: Session, *, enquiry_id: uuid.UUID, job_id: uuid.UUID, notification_id: uuid.UUID, operation_key: str
) -> uuid.UUID:
    return _insert_event(
        session, enquiry_id=enquiry_id, job_id=job_id,
        event_type="notification.sent", severity="info",
        details={"notification_id": str(notification_id), "operation_key": operation_key},
    )


def insert_unknown_event(
    session: Session, *, enquiry_id: uuid.UUID, job_id: uuid.UUID, notification_id: uuid.UUID, operation_key: str, error_code: str
) -> uuid.UUID:
    return _insert_event(
        session, enquiry_id=enquiry_id, job_id=job_id,
        event_type="notification.unknown", severity="warning",
        details={"notification_id": str(notification_id), "operation_key": operation_key, "error_code": error_code},
    )


def insert_failed_event(
    session: Session, *, enquiry_id: uuid.UUID, job_id: uuid.UUID, notification_id: uuid.UUID, operation_key: str, error_code: str
) -> uuid.UUID:
    return _insert_event(
        session, enquiry_id=enquiry_id, job_id=job_id,
        event_type="notification.failed", severity="error",
        details={"notification_id": str(notification_id), "operation_key": operation_key, "error_code": error_code},
    )
