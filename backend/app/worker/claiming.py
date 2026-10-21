"""Worker claiming, leasing, fencing and the local prepare."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from datetime import timedelta

from app.db import job_repo
from app.ports.clock import BusinessClock
from app.ports.crm import CrmPort, ProviderError
from app.ports.scheduler import Scheduler
from app.services import crm_sync
from app.services.scheduler import BackoffScheduler

# The retryable-before-write set for this slice, and the worker
# contract's jitter maximum (behavior.yaml worker.jitter_max_seconds).
RETRYABLE_CODES = frozenset({"connect_failed", "unavailable", "rate_limited"})
JITTER_MAX_SECONDS = 0.5

# behavior.yaml#worker.lease_seconds. Lease and heartbeat columns are
# OperationalClock-owned, so this duration is applied to the database
# clock (clock_timestamp), never to the BusinessClock.
LEASE_SECONDS = 60


@dataclass(frozen=True)
class ClaimedJob:
    """One claimed job and the lease that fences it."""

    job_id: uuid.UUID
    kind: str
    entity_id: uuid.UUID
    lease_token: uuid.UUID
    attempt_no: int
    cycle_no: int = 1


def claim_next(
    session: Session, worker_id: uuid.UUID, clock: BusinessClock
) -> ClaimedJob | None:
    """Claim the next due job, or return None when nothing is due.

    One short transaction, committed before returning: select the
    next claimable job FOR UPDATE SKIP LOCKED, recover its lease
    first if the previous one expired, then stamp the new lease and
    open the attempt row.
    """
    with session.begin():
        job = job_repo.select_claimable(session, clock.now_utc())
        if job is None:
            return None
        if job.state == "running":
            # Claimable only because its lease expired: record the
            # recovery before the new lease replaces the old token.
            job_repo.insert_lease_recovered_event(
                session,
                job_id=job.id,
                enquiry_id=job.entity_id,
                expired_lease_token=job.lease_token,
            )
        lease_token = uuid.uuid4()
        stamped = job_repo.stamp_lease(
            session, job.id, lease_token, LEASE_SECONDS
        )
        job_repo.insert_attempt(
            session,
            job_id=job.id,
            attempt_no=stamped.attempt_count,
            cycle_no=stamped.cycle_no,
            lease_token=lease_token,
        )
        return ClaimedJob(
            job_id=job.id,
            kind=job.kind,
            entity_id=job.entity_id,
            lease_token=lease_token,
            attempt_no=stamped.attempt_count,
            cycle_no=stamped.cycle_no,
        )


def handle_claim(
    session: Session,
    claim: ClaimedJob,
    clock: BusinessClock,
    crm: CrmPort | None = None,
    scheduler: Scheduler | None = None,
) -> None:
    """Execute one claimed job.

    The fence comes first for every kind: a conditional update keyed
    on the lease token that only touches updated_at. Zero rows means
    the lease is stale — return quietly, write nothing. Success is
    marked only after the work completes; a retryable CRM failure
    moves a sync job to waiting_retry instead. Any other failure
    rolls the whole claim back.
    """
    with session.begin():
        fenced = job_repo.fence_claim(session, claim.job_id, claim.lease_token)
        if fenced == 0:
            return
        if claim.kind == "sync_contact":
            if crm is None:
                raise ValueError("sync_contact requires a CRM port")
            effective_scheduler = scheduler or BackoffScheduler()
            try:
                crm_sync.sync_contact(session, claim, crm, clock)
            except ProviderError as err:
                if err.code not in RETRYABLE_CODES:
                    raise
                if err.retry_after_seconds is not None:
                    delay = effective_scheduler.parse_retry_after(
                        str(err.retry_after_seconds), clock.now_utc()
                    )
                else:
                    delay = effective_scheduler.compute_backoff(
                        claim.attempt_no, JITTER_MAX_SECONDS
                    )
                next_attempt_at = clock.now_utc() + timedelta(seconds=delay)
                job_repo.close_attempt(
                    session,
                    job_id=claim.job_id,
                    attempt_no=claim.attempt_no,
                    outcome="failed",
                    error_code=err.code,
                )
                job_repo.schedule_job_retry(
                    session,
                    claim.job_id,
                    next_attempt_at=next_attempt_at,
                    error_code=err.code,
                )
                job_repo.insert_retry_scheduled_event(
                    session,
                    job_id=claim.job_id,
                    enquiry_id=claim.entity_id,
                    next_attempt_at=next_attempt_at,
                    attempt_no=claim.attempt_no,
                )
                return
        elif claim.kind == "prepare_enquiry":
            prepared_at = clock.now_utc()
            prepared = job_repo.stamp_local_prepared(
                session, claim.entity_id, prepared_at
            )
            if prepared is None:
                return
            job_repo.insert_prepared_event(
                session,
                enquiry_id=claim.entity_id,
                owner_id=prepared.owner_id,
                routing_reason=prepared.routing_reason,
            )
            job_repo.insert_sync_job(
                session,
                enquiry_id=claim.entity_id,
                contact_id=prepared.contact_id,
                next_attempt_at=prepared_at,
            )
        else:
            # Roll back the fence update rather than mark an
            # unhandled job succeeded.
            raise ValueError(f"unhandled job kind: {claim.kind!r}")
        job_repo.mark_job_succeeded(session, claim.job_id)
        job_repo.close_attempt(
            session,
            job_id=claim.job_id,
            attempt_no=claim.attempt_no,
            outcome="succeeded",
        )
