"""Repository functions for worker claiming and handling."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import tables


def select_claimable(session: Session, due_before: datetime) -> Any | None:
    return session.execute(
        select(
            tables.jobs.c.id,
            tables.jobs.c.kind,
            tables.jobs.c.entity_id,
            tables.jobs.c.state,
            tables.jobs.c.lease_token,
            tables.jobs.c.cycle_no,
        )
        .where(
            (
                (tables.jobs.c.state == "pending")
                & (tables.jobs.c.next_attempt_at <= due_before)
            )
            | (
                (tables.jobs.c.state == "running")
                & (tables.jobs.c.lease_until < text("now()"))
            )
            | (
                (tables.jobs.c.state == "waiting_retry")
                & (tables.jobs.c.next_attempt_at <= due_before)
            )
            | (
                (tables.jobs.c.state == "reconciling")
                & (tables.jobs.c.next_attempt_at <= due_before)
            )
        )
        .order_by(
            tables.jobs.c.next_attempt_at,
            tables.jobs.c.created_at,
            tables.jobs.c.id,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    ).one_or_none()


def get_job_state(session: Session, job_id: uuid.UUID) -> str | None:
    """Return the persisted job state, or None when no such job exists.

    A plain read: it opens a read transaction the caller is
    responsible for closing (see run_once in app/worker/runner).
    """
    return session.execute(
        select(tables.jobs.c.state).where(tables.jobs.c.id == job_id)
    ).scalar_one_or_none()


def stamp_lease(
    session: Session, job_id: uuid.UUID, lease_token: uuid.UUID, lease_seconds: int
) -> Any:
    return session.execute(
        update(tables.jobs)
        .where(tables.jobs.c.id == job_id)
        .values(
            state="running",
            lease_token=lease_token,
            lease_until=text(
                f"clock_timestamp() + interval '{lease_seconds} seconds'"
            ),
            heartbeat_at=text("clock_timestamp()"),
            updated_at=text("clock_timestamp()"),
            attempt_count=tables.jobs.c.attempt_count + 1,
            cycle_attempt_count=tables.jobs.c.cycle_attempt_count + 1,
        )
        .returning(
            tables.jobs.c.attempt_count,
            tables.jobs.c.cycle_no,
            tables.jobs.c.cycle_attempt_count,
        )
    ).one()


def insert_attempt(
    session: Session,
    *,
    job_id: uuid.UUID,
    attempt_no: int,
    cycle_no: int,
    lease_token: uuid.UUID,
) -> uuid.UUID:
    return session.execute(
        insert(tables.job_attempts)
        .values(
            job_id=job_id,
            attempt_no=attempt_no,
            cycle_no=cycle_no,
            lease_token=lease_token,
        )
        .returning(tables.job_attempts.c.id)
    ).scalar_one()


def insert_lease_recovered_event(
    session: Session,
    *,
    job_id: uuid.UUID,
    enquiry_id: uuid.UUID,
    expired_lease_token: uuid.UUID,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            job_id=job_id,
            event_type="job.lease_recovered",
            severity="warning",
            actor_type="worker",
            details_json={
                "job_id": str(job_id),
                "expired_lease_token": str(expired_lease_token),
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def fence_claim(
    session: Session, job_id: uuid.UUID, lease_token: uuid.UUID
) -> int:
    result = session.execute(
        update(tables.jobs)
        .where(
            tables.jobs.c.id == job_id,
            tables.jobs.c.lease_token == lease_token,
            tables.jobs.c.state == "running",
        )
        .values(updated_at=text("clock_timestamp()"))
        .returning(tables.jobs.c.id)
    )
    return len(result.fetchall())


def _job_where(job_id: uuid.UUID, lease_token: uuid.UUID | None):
    """WHERE for a job update, optionally fenced on the lease.

    A supplied lease_token adds AND lease_token = :token, so a
    worker whose lease was stolen updates zero rows. The default
    None keeps existing callers working unchanged.
    """
    conditions = [tables.jobs.c.id == job_id]
    if lease_token is not None:
        conditions.append(tables.jobs.c.lease_token == lease_token)
    return conditions


def mark_job_succeeded(
    session: Session, job_id: uuid.UUID, *, lease_token: uuid.UUID | None = None
) -> int:
    result = session.execute(
        update(tables.jobs)
        .where(*_job_where(job_id, lease_token))
        .values(
            state="succeeded",
            last_error_code=None,
            updated_at=text("clock_timestamp()"),
        )
    )
    return result.rowcount


def schedule_job_retry(
    session: Session,
    job_id: uuid.UUID,
    *,
    next_attempt_at: datetime,
    error_code: str,
    lease_token: uuid.UUID | None = None,
) -> int:
    result = session.execute(
        update(tables.jobs)
        .where(*_job_where(job_id, lease_token))
        .values(
            state="waiting_retry",
            next_attempt_at=next_attempt_at,
            last_error_code=error_code,
            lease_token=None,
            lease_until=None,
            heartbeat_at=None,
            updated_at=text("clock_timestamp()"),
        )
    )
    return result.rowcount


def move_job_to_blocked(
    session: Session,
    job_id: uuid.UUID,
    *,
    error_code: str,
    lease_token: uuid.UUID | None = None,
) -> int:
    result = session.execute(
        update(tables.jobs)
        .where(*_job_where(job_id, lease_token))
        .values(
            state="blocked",
            last_error_code=error_code,
            lease_token=None,
            lease_until=None,
            heartbeat_at=None,
            updated_at=text("clock_timestamp()"),
        )
    )
    return result.rowcount


def move_job_to_dead(
    session: Session,
    job_id: uuid.UUID,
    *,
    error_code: str,
    lease_token: uuid.UUID | None = None,
) -> int:
    result = session.execute(
        update(tables.jobs)
        .where(*_job_where(job_id, lease_token))
        .values(
            state="dead",
            last_error_code=error_code,
            lease_token=None,
            lease_until=None,
            heartbeat_at=None,
            updated_at=text("clock_timestamp()"),
        )
    )
    return result.rowcount


def move_job_to_reconciling(
    session: Session,
    job_id: uuid.UUID,
    *,
    next_attempt_at: datetime,
    error_code: str,
    lease_token: uuid.UUID | None = None,
) -> int:
    result = session.execute(
        update(tables.jobs)
        .where(*_job_where(job_id, lease_token))
        .values(
            state="reconciling",
            next_attempt_at=next_attempt_at,
            last_error_code=error_code,
            lease_token=None,
            lease_until=None,
            heartbeat_at=None,
            updated_at=text("clock_timestamp()"),
        )
    )
    return result.rowcount


def reschedule_reconciling_job(
    session: Session,
    job_id: uuid.UUID,
    *,
    next_attempt_at: datetime,
    lease_token: uuid.UUID | None = None,
) -> int:
    result = session.execute(
        update(tables.jobs)
        .where(*_job_where(job_id, lease_token))
        .values(
            state="reconciling",
            next_attempt_at=next_attempt_at,
            lease_token=None,
            lease_until=None,
            heartbeat_at=None,
            updated_at=text("clock_timestamp()"),
        )
    )
    return result.rowcount


def insert_retry_scheduled_event(
    session: Session,
    *,
    job_id: uuid.UUID,
    enquiry_id: uuid.UUID,
    next_attempt_at: datetime,
    attempt_no: int,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            job_id=job_id,
            event_type="job.retry_scheduled",
            severity="warning",
            actor_type="worker",
            details_json={
                "job_id": str(job_id),
                "next_attempt_at": next_attempt_at.isoformat(),
                "attempt_no": attempt_no,
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def insert_job_failed_event(
    session: Session,
    *,
    job_id: uuid.UUID,
    enquiry_id: uuid.UUID,
    error_code: str,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            job_id=job_id,
            event_type="job.failed",
            severity="error",
            actor_type="worker",
            details_json={
                "job_id": str(job_id),
                "error_code": error_code,
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def stamp_local_prepared(
    session: Session, enquiry_id: uuid.UUID, prepared_at: datetime
) -> Any | None:
    return session.execute(
        update(tables.enquiries)
        .where(
            tables.enquiries.c.id == enquiry_id,
            tables.enquiries.c.local_prepared_at.is_(None),
        )
        .values(local_prepared_at=prepared_at)
        .returning(
            tables.enquiries.c.owner_id,
            tables.enquiries.c.routing_reason,
            tables.enquiries.c.contact_id,
        )
    ).one_or_none()


def insert_sync_job(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    contact_id: uuid.UUID,
    next_attempt_at: datetime,
) -> None:
    stmt = (
        pg_insert(tables.jobs)
        .values(
            kind="sync_contact",
            entity_id=enquiry_id,
            dedupe_key=f"sync_contact:{contact_id}",
            state="pending",
            next_attempt_at=next_attempt_at,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    session.execute(stmt)


def insert_prepared_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    routing_reason: str | None,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="enquiry.prepared",
            severity="info",
            actor_type="worker",
            details_json={
                "owner_id": str(owner_id) if owner_id is not None else None,
                "routing_reason": routing_reason,
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def close_attempt(
    session: Session,
    *,
    job_id: uuid.UUID,
    attempt_no: int,
    outcome: str,
    error_code: str | None = None,
) -> int:
    result = session.execute(
        update(tables.job_attempts)
        .where(
            tables.job_attempts.c.job_id == job_id,
            tables.job_attempts.c.attempt_no == attempt_no,
        )
        .values(
            finished_at=text("clock_timestamp()"),
            outcome=outcome,
            error_code=error_code,
        )
    )
    return result.rowcount
