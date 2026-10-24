"""Operator retry of a dead or blocked job (contract A18)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db import job_repo
from app.ports.clock import BusinessClock
from app.services.errors import JobNotFound

__all__ = ["JobNotFound", "JobRetryConflict", "retry_job"]


class JobRetryConflict(Exception):
    """The retry's optimistic-concurrency preconditions failed.

    Carries the envelope code so the API layer can distinguish a
    state mismatch ("invalid_state") from a stale
    expected_updated_at ("version_conflict") in the message.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def retry_job(
    session: Session,
    job_id: uuid.UUID,
    *,
    expected_state: str,
    expected_updated_at: datetime,
    reason: str,
    clock: BusinessClock,
    operator_id: uuid.UUID,
) -> Any:
    """Reopen a job in a new cycle, or explain why not.

    One transaction: the conditional transition and the single
    job.manual_retry event commit together, so a conflicted call
    writes nothing at all - no transition and no event.
    external_operations is never touched: the retry changes the
    job's cycle, never the operation's status, so an unknown
    operation still routes the next claim to readback and a
    failed or absent one re-enters begin_sync.
    """
    next_attempt_at = clock.now_utc()
    with session.begin():
        row = job_repo.retry_job_transition(
            session,
            job_id,
            expected_state=expected_state,
            expected_updated_at=expected_updated_at,
            next_attempt_at=next_attempt_at,
        )
        if row is not None:
            job_repo.insert_manual_retry_event(
                session,
                job_id=row.id,
                enquiry_id=row.entity_id,
                operator_id=operator_id,
                reason=reason,
                cycle_no=row.cycle_no,
            )
            return row

        existing = job_repo.get_job_row(session, job_id)
        if existing is None:
            raise JobNotFound(f"job not found: {job_id}")
        if existing.state != expected_state:
            raise JobRetryConflict(
                "invalid_state",
                f"job {job_id} is in state {existing.state!r}, "
                f"not the expected {expected_state!r}",
            )
        raise JobRetryConflict(
            "version_conflict",
            f"job {job_id} changed at {existing.updated_at}, "
            "not at the expected updated_at",
        )
