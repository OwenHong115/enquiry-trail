"""Repository functions for provider health (no commits here)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import tables


def get_health(session: Session, provider: str) -> Any | None:
    return session.execute(
        select(
            tables.integration_health.c.status,
            tables.integration_health.c.incident_id,
        ).where(tables.integration_health.c.provider == provider)
    ).one_or_none()


def insert_health_blocked(
    session: Session, provider: str, incident_id: uuid.UUID
) -> None:
    session.execute(
        insert(tables.integration_health).values(
            provider=provider,
            status="blocked",
            incident_id=incident_id,
            last_error_code="auth_failed",
            blocked_at=text("clock_timestamp()"),
            updated_at=text("clock_timestamp()"),
        )
    )


def try_insert_health_blocked(
    session: Session, provider: str, incident_id: uuid.UUID
) -> uuid.UUID | None:
    """Insert a blocked row, or report the conflict without raising.

    INSERT ... ON CONFLICT (provider) DO NOTHING: the statement
    waits for any concurrent inserter to commit and then reports
    no row, so exactly one contender ever inserts. Returns the
    new incident id when this call inserted, else None.
    """
    stmt = (
        pg_insert(tables.integration_health)
        .values(
            provider=provider,
            status="blocked",
            incident_id=incident_id,
            last_error_code="auth_failed",
            blocked_at=text("clock_timestamp()"),
            updated_at=text("clock_timestamp()"),
        )
        .on_conflict_do_nothing(index_elements=["provider"])
        .returning(tables.integration_health.c.incident_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_health_for_update(session: Session, provider: str) -> Any | None:
    """Read the health row under FOR UPDATE (call after a conflict)."""
    return session.execute(
        select(
            tables.integration_health.c.status,
            tables.integration_health.c.incident_id,
        )
        .where(tables.integration_health.c.provider == provider)
        .with_for_update()
    ).one_or_none()


def update_health_blocked(
    session: Session, provider: str, incident_id: uuid.UUID
) -> None:
    session.execute(
        update(tables.integration_health)
        .where(tables.integration_health.c.provider == provider)
        .values(
            status="blocked",
            incident_id=incident_id,
            last_error_code="auth_failed",
            blocked_at=text("clock_timestamp()"),
            updated_at=text("clock_timestamp()"),
        )
    )


def set_health_ok(session: Session, provider: str) -> None:
    session.execute(
        update(tables.integration_health)
        .where(tables.integration_health.c.provider == provider)
        .values(
            status="ok",
            blocked_at=None,
            last_error_code=None,
            updated_at=text("clock_timestamp()"),
        )
    )


def earliest_blocked_sync_job(session: Session) -> uuid.UUID | None:
    return session.execute(
        select(tables.jobs.c.id)
        .where(
            tables.jobs.c.kind == "sync_contact",
            tables.jobs.c.state == "blocked",
        )
        .order_by(tables.jobs.c.created_at, tables.jobs.c.id)
        .limit(1)
    ).scalar_one_or_none()


def requeue_blocked_sync_jobs(
    session: Session, *, next_attempt_at: datetime
) -> int:
    result = session.execute(
        update(tables.jobs)
        .where(
            tables.jobs.c.kind == "sync_contact",
            tables.jobs.c.state == "blocked",
        )
        .values(
            state="pending",
            next_attempt_at=next_attempt_at,
            cycle_no=tables.jobs.c.cycle_no + 1,
            cycle_attempt_count=0,
            last_error_code=None,
            lease_token=None,
            lease_until=None,
            heartbeat_at=None,
            updated_at=text("clock_timestamp()"),
        )
    )
    return result.rowcount


def insert_blocked_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    provider: str,
    incident_id: uuid.UUID,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="integration.blocked",
            severity="error",
            actor_type="worker",
            details_json={"provider": provider, "incident_id": str(incident_id)},
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def insert_resumed_event(
    session: Session,
    *,
    provider: str,
    incident_id: uuid.UUID | None,
    operator_id: uuid.UUID,
    reason: str,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=None,
            event_type="integration.resumed",
            severity="info",
            actor_type="operator",
            actor_id=operator_id,
            details_json={
                "provider": provider,
                "incident_id": str(incident_id) if incident_id else None,
                "reason": reason,
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()
