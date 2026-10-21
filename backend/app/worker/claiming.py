"""Worker claiming, leasing, fencing and the local prepare."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db import job_repo
from app.ports.clock import BusinessClock
from app.ports.crm import CrmPort
from app.services import crm_sync

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
        )


def handle_claim(
    session: Session,
    claim: ClaimedJob,
    clock: BusinessClock,
    crm: CrmPort | None = None,
) -> None:
    """Execute one claimed job.

    The fence comes first for the local prepare: a single conditional
    update keyed on the lease token. Zero rows means the lease is
    stale — write nothing, emit nothing, return quietly. CRM sync
    jobs dispatch to the sync service instead.
    """
    with session.begin():
        if claim.kind == "sync_contact":
            crm_sync.sync_contact(session, claim.entity_id, crm, clock)
            return
        fenced = job_repo.fence_succeed_job(session, claim.job_id, claim.lease_token)
        if fenced == 0:
            return
        if claim.kind != "prepare_enquiry":
            # Roll back the fence update rather than mark an
            # unhandled job succeeded.
            raise ValueError(f"unhandled job kind: {claim.kind!r}")
        prepared = job_repo.stamp_local_prepared(
            session, claim.entity_id, clock.now_utc()
        )
        if prepared is None:
            return
        job_repo.insert_prepared_event(
            session,
            enquiry_id=claim.entity_id,
            owner_id=prepared.owner_id,
            routing_reason=prepared.routing_reason,
        )
        job_repo.close_attempt(
            session,
            job_id=claim.job_id,
            attempt_no=claim.attempt_no,
            outcome="succeeded",
        )
