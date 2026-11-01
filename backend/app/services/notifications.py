"""Notification service: enqueue on ready, deliver exactly once.

A notification is due when an enquiry reaches ready/synced. The
worker runner calls ``maybe_enqueue_ready_notification`` in its own
transaction after a sync job succeeds, so notification work never
blocks or rolls back intake, prepare or CRM sync. Delivery runs in
the notify_ready job: success, delivery-unknown and definite failure
are terminal for the notification (no automatic resend of an unknown
result); retryable failures follow the existing job retry machinery.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.db import job_repo, notification_repo
from app.ports.notifications import (
    NotificationContext,
    NotificationPort,
    ReadyNotification,
)
from app.ports.scheduler import Scheduler
from app.services.scheduler import BackoffScheduler

RETRYABLE_CODES = frozenset({"connect_failed", "unavailable", "rate_limited"})
PERMANENT_CODES = frozenset(
    {
        "validation_failed",
        "not_found",
        "conflict",
        "invalid_response",
        "director_unavailable",
        "auth_failed",
    }
)
JITTER_MAX_SECONDS = 0.5
HTTP_DEADLINE_SECONDS = 15


def operation_key_for(enquiry_id: uuid.UUID) -> str:
    return notification_repo.operation_key_for(enquiry_id)


def enqueue_ready_notification(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    enabled: bool,
    now: datetime,
):
    """Create the pending row and notify job once, or nothing disabled.

    Idempotent on the notification operation key and the job dedupe
    key: re-delivery of the same ready event creates no second row
    and no second job. Caller owns the transaction.
    """
    if not enabled:
        return None
    row = notification_repo.ensure_notification(session, enquiry_id)
    notification_repo.enqueue_notify_job(
        session, enquiry_id=enquiry_id, next_attempt_at=now
    )
    return row


def maybe_enqueue_ready_notification(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    enabled: bool,
    now: datetime,
):
    """Enqueue only when the enquiry is actually ready/synced."""
    if not enabled:
        return None
    if not notification_repo.is_enquiry_ready(session, enquiry_id):
        return None
    return enqueue_ready_notification(session, enquiry_id, enabled=True, now=now)


def _error_code(err: BaseException) -> str | None:
    code = getattr(err, "code", None)
    return code if isinstance(code, str) else None


def handle_notify_claim(
    session: Session,
    claim,
    clock,
    notifications: NotificationPort,
    scheduler: Scheduler | None = None,
) -> None:
    """Execute one claimed notify_ready job.

    The fence comes first. An already-sent or delivery-unknown
    notification is never resent: the job simply succeeds. Provider
    calls happen outside any open transaction.
    """
    scheduler = scheduler or BackoffScheduler()
    enquiry_id = claim.entity_id

    # Block 1: fence, ensure the row, decide whether to send at all.
    message: ReadyNotification | None = None
    notification_id: uuid.UUID | None = None
    with session.begin():
        if job_repo.fence_claim(session, claim.job_id, claim.lease_token) == 0:
            return
        row = notification_repo.ensure_notification(session, enquiry_id)
        notification_id = row.id
        if row.status in ("sent", "delivery_unknown"):
            job_repo.mark_job_succeeded(
                session, claim.job_id, lease_token=claim.lease_token
            )
            job_repo.close_attempt(
                session, job_id=claim.job_id, attempt_no=claim.attempt_no,
                outcome="succeeded",
            )
            return
        display = notification_repo.get_enquiry_display(session, enquiry_id)
        display_no = display.display_no if display is not None else None
        service_type = display.service_type if display is not None else ""
        label = f"#{display_no}" if display_no is not None else str(enquiry_id)
        message = ReadyNotification(
            enquiry_id=enquiry_id,
            operation_key=row.operation_key,
            text=f"Enquiry {label} ({service_type}) is ready".strip(),
        )

    assert message is not None and notification_id is not None
    context = NotificationContext(
        job_id=claim.job_id,
        cycle_no=claim.cycle_no,
        attempt_no=claim.attempt_no,
        deadline_seconds=HTTP_DEADLINE_SECONDS,
    )
    try:
        notifications.send_ready(message, context)
    except Exception as err:  # classified provider failures only
        code = _error_code(err)
        if code is None:
            raise
        if code == "result_unknown":
            with session.begin():
                if job_repo.fence_claim(session, claim.job_id, claim.lease_token) == 0:
                    return
                # Re-check: another worker may have resolved it.
                current = notification_repo.get_notification(session, enquiry_id)
                if current is not None and current.status == "sent":
                    job_repo.mark_job_succeeded(
                        session, claim.job_id, lease_token=claim.lease_token
                    )
                    job_repo.close_attempt(
                        session, job_id=claim.job_id,
                        attempt_no=claim.attempt_no, outcome="succeeded",
                    )
                    return
                notification_repo.mark_delivery_unknown(session, notification_id, code)
                notification_repo.insert_unknown_event(
                    session, enquiry_id=enquiry_id, job_id=claim.job_id,
                    notification_id=notification_id,
                    operation_key=message.operation_key, error_code=code,
                )
                # Terminal: no automatic resend of an unknown result.
                job_repo.mark_job_succeeded(
                    session, claim.job_id, lease_token=claim.lease_token
                )
                job_repo.close_attempt(
                    session, job_id=claim.job_id,
                    attempt_no=claim.attempt_no, outcome="delivery_unknown",
                    error_code=code,
                )
            return
        if code in RETRYABLE_CODES:
            retry_after = getattr(err, "retry_after_seconds", None)
            if retry_after is not None:
                delay = scheduler.parse_retry_after(str(retry_after), clock.now_utc())
            else:
                delay = scheduler.compute_backoff(
                    claim.cycle_attempt_count, JITTER_MAX_SECONDS
                )
            next_attempt_at = clock.now_utc() + timedelta(seconds=delay)
            with session.begin():
                if job_repo.fence_claim(session, claim.job_id, claim.lease_token) == 0:
                    return
                notification_repo.record_attempt_error(session, notification_id, code)
                job_repo.close_attempt(
                    session, job_id=claim.job_id,
                    attempt_no=claim.attempt_no, outcome="failed", error_code=code,
                )
                job_repo.schedule_job_retry(
                    session, claim.job_id, next_attempt_at=next_attempt_at,
                    error_code=code, lease_token=claim.lease_token,
                )
                job_repo.insert_retry_scheduled_event(
                    session, job_id=claim.job_id, enquiry_id=enquiry_id,
                    next_attempt_at=next_attempt_at, attempt_no=claim.attempt_no,
                )
            return
        # Definite failure (permanent vocabulary, and any other
        # classified code): record failed, close the job dead.
        with session.begin():
            if job_repo.fence_claim(session, claim.job_id, claim.lease_token) == 0:
                return
            notification_repo.mark_failed(session, notification_id, code)
            notification_repo.insert_failed_event(
                session, enquiry_id=enquiry_id, job_id=claim.job_id,
                notification_id=notification_id,
                operation_key=message.operation_key, error_code=code,
            )
            job_repo.close_attempt(
                session, job_id=claim.job_id,
                attempt_no=claim.attempt_no, outcome="failed", error_code=code,
            )
            job_repo.move_job_to_dead(
                session, claim.job_id, error_code=code, lease_token=claim.lease_token
            )
            job_repo.insert_job_failed_event(
                session, job_id=claim.job_id, enquiry_id=enquiry_id, error_code=code
            )
        return

    # Success: exactly one sent event, guarded by the status check.
    with session.begin():
        if job_repo.fence_claim(session, claim.job_id, claim.lease_token) == 0:
            return
        current = notification_repo.get_notification(session, enquiry_id)
        if current is not None and current.status == "sent":
            job_repo.mark_job_succeeded(
                session, claim.job_id, lease_token=claim.lease_token
            )
            job_repo.close_attempt(
                session, job_id=claim.job_id,
                attempt_no=claim.attempt_no, outcome="succeeded",
            )
            return
        notification_repo.mark_sent(session, notification_id)
        notification_repo.insert_sent_event(
            session, enquiry_id=enquiry_id, job_id=claim.job_id,
            notification_id=notification_id, operation_key=message.operation_key,
        )
        job_repo.mark_job_succeeded(
            session, claim.job_id, lease_token=claim.lease_token
        )
        job_repo.close_attempt(
            session, job_id=claim.job_id,
            attempt_no=claim.attempt_no, outcome="succeeded",
        )
