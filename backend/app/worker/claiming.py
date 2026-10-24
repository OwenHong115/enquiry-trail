"""Worker claiming, leasing, fencing and the local prepare."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from datetime import timedelta

from app.db import crm_repo, job_repo
from app.logging import log_event
from app.ports.clock import BusinessClock
from app.ports.crm import (
    PROVIDER_ERROR_CODES,
    CrmPort,
    FoundContact,
    NotFound,
    ProviderError,
)
from app.ports.scheduler import Scheduler
from app.services import crm_sync, health
from app.services.scheduler import BackoffScheduler

# The retryable-before-write set for this slice, and the worker
# contract's jitter maximum (behavior.yaml worker.jitter_max_seconds).
RETRYABLE_CODES = frozenset({"connect_failed", "unavailable", "rate_limited"})
PERMANENT_CODES = frozenset(
    {
        "validation_failed",
        "not_found",
        "conflict",
        "invalid_response",
        "director_unavailable",
    }
)
JITTER_MAX_SECONDS = 0.5

# behavior.yaml#worker.per_cycle_max_attempts. The schema mirrors this
# as CHECK(cycle_attempt_count BETWEEN 0 AND 4), so the fourth
# retryable failure in a cycle must end the job instead of rescheduling.
# Counting rule: every claim consumes one unit of the per-cycle
# budget, whether it dispatches a create or a readback. The retry
# branch in _record_provider_failure enforces the ceiling in-cycle;
# the claim-time closure in claim_next is the backstop that also
# covers crashes and readback loops.
PER_CYCLE_MAX_ATTEMPTS = 4

# behavior.yaml worker.reconciliation_read_delays_seconds (1/4/16):
# the first readback follows the unknown at +1s, the next at +4s.
FIRST_READBACK_DELAY_SECONDS = 1
READBACK_DELAY_SECONDS = 4

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
    cycle_attempt_count: int = 1


def _close_at_budget_ceiling(session: Session, job: Any) -> None:
    """Close a job that is already at the per-cycle budget ceiling.

    Runs inside the claim transaction, whose SELECT ... FOR UPDATE
    row lock is the exclusivity: this is not the finalization of a
    claimed job, so there is no lease to re-check and none is
    invented. Writes no job_attempts row, and leaves
    external_operations untouched, so a reconciling job closed
    here keeps its operation "unknown" for a future operator
    retry. The job's existing last_error_code is kept when it is
    in the provider vocabulary; otherwise "unavailable" stands in.
    """
    error_code = (
        job.last_error_code
        if job.last_error_code in PROVIDER_ERROR_CODES
        else "unavailable"
    )
    job_repo.move_job_to_dead(session, job.id, error_code=error_code)
    job_repo.insert_job_failed_event(
        session,
        job_id=job.id,
        enquiry_id=job.entity_id,
        error_code=error_code,
    )


def claim_next(
    session: Session, worker_id: uuid.UUID, clock: BusinessClock
) -> ClaimedJob | None:
    """Claim the next due job, or return None when nothing is due.

    One short transaction, committed before returning: select the
    next claimable job FOR UPDATE SKIP LOCKED, recover its lease
    first if the previous one expired, then stamp the new lease and
    open the attempt row.

    A selected job already at the per-cycle budget ceiling is
    closed terminally instead of stamped (stamping would take
    cycle_attempt_count past the schema CHECK), and the loop keeps
    scanning for the next claimable job in the same call, so a
    poisoned job neither raises nor wedges the queue.
    """
    with session.begin():
        while True:
            job = job_repo.select_claimable(session, clock.now_utc())
            if job is None:
                return None
            if job.cycle_attempt_count >= PER_CYCLE_MAX_ATTEMPTS:
                _close_at_budget_ceiling(session, job)
                continue
            break
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
            cycle_attempt_count=stamped.cycle_attempt_count,
        )


def _lease_still_held(session: Session, claim: ClaimedJob) -> bool:
    """Re-check the lease inside a finalization transaction.

    Must be called inside ``with session.begin():`` before any
    write. True means the fence update matched this claim's lease
    and the block may proceed under the job row lock. False means
    another worker now owns the job: the caller abandons the whole
    block without writing (the empty transaction commits), closes
    nothing, and does not raise. Only identifiers are logged.
    """
    if job_repo.fence_claim(session, claim.job_id, claim.lease_token) != 0:
        return True
    log_event(
        "job.fence_lost", job_id=str(claim.job_id), attempt_no=claim.attempt_no
    )
    return False


def _record_provider_failure(
    session: Session,
    claim: ClaimedJob,
    clock: BusinessClock,
    scheduler: Scheduler,
    err: ProviderError,
    *,
    operation_id: uuid.UUID | None,
    mark_operation_failed: bool,
) -> bool:
    """Record a provider failure in its own committed block.

    Total over the provider vocabulary for the create and lookup
    paths: every code returns True here, so the caller never
    re-raises a ProviderError. False is returned only for a code
    outside the policy (defensive; the vocabulary is closed), and
    the caller re-raises then; the committed intent (if any) stays.
    Returns True when the failure was recorded, and also True when
    the lease fence was lost: then nothing was written and the
    caller must still not re-raise, because another worker owns
    the job now and will record the outcome itself.
    """
    if err.code == "auth_failed":
        # Never retried: block the job and the provider.
        with session.begin():
            if not _lease_still_held(session, claim):
                return True
            if mark_operation_failed and operation_id is not None:
                # Create path only (the caller passed an operation
                # to mark): a 401 is a definite pre-write rejection
                # (http_401_before_write), so close the operation as
                # failed rather than leaving it at intent - an
                # unresolved operation would send the post-resume
                # claim down the readback-only path forever. The
                # lookup path passes no operation and is untouched.
                crm_repo.set_operation_failed(session, operation_id)
            job_repo.close_attempt(
                session,
                job_id=claim.job_id,
                attempt_no=claim.attempt_no,
                outcome="blocked",
                error_code="auth_failed",
            )
            job_repo.move_job_to_blocked(
                session,
                claim.job_id,
                error_code="auth_failed",
                lease_token=claim.lease_token,
            )
            health.block_provider(session, enquiry_id=claim.entity_id)
        return True
    if mark_operation_failed and err.code == "result_unknown":
        assert operation_id is not None
        with session.begin():
            if not _lease_still_held(session, claim):
                return True
            crm_repo.set_operation_unknown(session, operation_id)
            job_repo.close_attempt(
                session,
                job_id=claim.job_id,
                attempt_no=claim.attempt_no,
                outcome="failed",
                error_code="result_unknown",
            )
            job_repo.move_job_to_reconciling(
                session,
                claim.job_id,
                next_attempt_at=clock.now_utc()
                + timedelta(seconds=FIRST_READBACK_DELAY_SECONDS),
                error_code="result_unknown",
                lease_token=claim.lease_token,
            )
            crm_repo.insert_operation_unknown_event(
                session,
                job_id=claim.job_id,
                enquiry_id=claim.entity_id,
                operation_id=operation_id,
            )
        return True
    if err.code in PERMANENT_CODES:
        # Permanent provider failure: terminal, in one fenced
        # block. The operation is marked failed only when the
        # caller passed mark_operation_failed (the create path);
        # a lookup-path failure has no operation at all.
        with session.begin():
            if not _lease_still_held(session, claim):
                return True
            if mark_operation_failed and operation_id is not None:
                crm_repo.set_operation_failed(session, operation_id)
            job_repo.close_attempt(
                session,
                job_id=claim.job_id,
                attempt_no=claim.attempt_no,
                outcome="failed",
                error_code=err.code,
            )
            job_repo.move_job_to_dead(
                session,
                claim.job_id,
                error_code=err.code,
                lease_token=claim.lease_token,
            )
            job_repo.insert_job_failed_event(
                session,
                job_id=claim.job_id,
                enquiry_id=claim.entity_id,
                error_code=err.code,
            )
        return True
    if err.code not in RETRYABLE_CODES:
        return False
    if claim.cycle_attempt_count >= PER_CYCLE_MAX_ATTEMPTS:
        # R1 budget exhausted for this cycle: die terminally instead
        # of rescheduling, even when the provider sent a retry_after.
        with session.begin():
            if not _lease_still_held(session, claim):
                return True
            if mark_operation_failed and operation_id is not None:
                crm_repo.set_operation_failed(session, operation_id)
            job_repo.close_attempt(
                session,
                job_id=claim.job_id,
                attempt_no=claim.attempt_no,
                outcome="failed",
                error_code=err.code,
            )
            job_repo.move_job_to_dead(
                session,
                claim.job_id,
                error_code=err.code,
                lease_token=claim.lease_token,
            )
            job_repo.insert_job_failed_event(
                session,
                job_id=claim.job_id,
                enquiry_id=claim.entity_id,
                error_code=err.code,
            )
        return True
    if err.retry_after_seconds is not None:
        delay = scheduler.parse_retry_after(
            str(err.retry_after_seconds), clock.now_utc()
        )
    else:
        delay = scheduler.compute_backoff(
            claim.cycle_attempt_count, JITTER_MAX_SECONDS
        )
    next_attempt_at = clock.now_utc() + timedelta(seconds=delay)
    with session.begin():
        if not _lease_still_held(session, claim):
            return True
        if mark_operation_failed and operation_id is not None:
            crm_repo.set_operation_failed(session, operation_id)
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
            lease_token=claim.lease_token,
        )
        job_repo.insert_retry_scheduled_event(
            session,
            job_id=claim.job_id,
            enquiry_id=claim.entity_id,
            next_attempt_at=next_attempt_at,
            attempt_no=claim.attempt_no,
        )
    return True


def _handle_readback(
    session: Session,
    claim: ClaimedJob,
    clock: BusinessClock,
    crm: CrmPort,
    contact: object,
    operation_id: uuid.UUID,
    scheduler: Scheduler,
) -> None:
    """Read back an unresolved operation; never dispatches a create.

    A failed readback never raises and never touches the
    operation's status: retryable codes reschedule the next
    readback, permanent codes close the job dead, and the
    operation stays "unknown" for a future operator retry in
    every cell. All finalizations are fenced per R8.
    """
    context = crm_sync.make_context(claim, operation_id)
    try:
        found = crm.get_contact_by_email(contact.email_norm, context)  # type: ignore[attr-defined]
    except ProviderError as err:
        if err.code in RETRYABLE_CODES:
            # Stay reconciling: close this attempt and reschedule
            # the next readback. rate_limited honours Retry-After
            # through the scheduler's existing 300s cap.
            if err.retry_after_seconds is not None:
                delay = scheduler.parse_retry_after(
                    str(err.retry_after_seconds), clock.now_utc()
                )
            else:
                delay = scheduler.compute_backoff(
                    claim.cycle_attempt_count, JITTER_MAX_SECONDS
                )
            with session.begin():
                if not _lease_still_held(session, claim):
                    return
                job_repo.close_attempt(
                    session,
                    job_id=claim.job_id,
                    attempt_no=claim.attempt_no,
                    outcome="readback_failed",
                    error_code=err.code,
                )
                job_repo.reschedule_reconciling_job(
                    session,
                    claim.job_id,
                    next_attempt_at=clock.now_utc()
                    + timedelta(seconds=delay),
                    lease_token=claim.lease_token,
                )
            return
        if err.code in PERMANENT_CODES:
            # Terminal, but the remote state is still unknown:
            # the operation is preserved, never marked failed.
            with session.begin():
                if not _lease_still_held(session, claim):
                    return
                job_repo.close_attempt(
                    session,
                    job_id=claim.job_id,
                    attempt_no=claim.attempt_no,
                    outcome="failed",
                    error_code=err.code,
                )
                job_repo.move_job_to_dead(
                    session,
                    claim.job_id,
                    error_code=err.code,
                    lease_token=claim.lease_token,
                )
                job_repo.insert_job_failed_event(
                    session,
                    job_id=claim.job_id,
                    enquiry_id=claim.entity_id,
                    error_code=err.code,
                )
            return
        if err.code != "auth_failed":
            raise
        # A 401 readback resolves nothing: block, leave unresolved.
        with session.begin():
            if not _lease_still_held(session, claim):
                return
            job_repo.close_attempt(
                session,
                job_id=claim.job_id,
                attempt_no=claim.attempt_no,
                outcome="blocked",
                error_code="auth_failed",
            )
            job_repo.move_job_to_blocked(
                session,
                claim.job_id,
                error_code="auth_failed",
                lease_token=claim.lease_token,
            )
            health.block_provider(session, enquiry_id=claim.entity_id)
        return
    if isinstance(found, NotFound):
        with session.begin():
            if not _lease_still_held(session, claim):
                return
            crm_repo.touch_operation_readback(session, operation_id)
            job_repo.close_attempt(
                session,
                job_id=claim.job_id,
                attempt_no=claim.attempt_no,
                outcome="readback_not_found",
            )
            job_repo.reschedule_reconciling_job(
                session,
                claim.job_id,
                next_attempt_at=clock.now_utc()
                + timedelta(seconds=READBACK_DELAY_SECONDS),
                lease_token=claim.lease_token,
            )
        return
    if not isinstance(found, FoundContact):  # pragma: no cover
        raise ValueError(f"unexpected CRM lookup result: {found!r}")
    with session.begin():
        if not _lease_still_held(session, claim):
            return
        crm_sync.record_readback_found(
            session, claim, contact, operation_id, found
        )
        job_repo.mark_job_succeeded(
            session, claim.job_id, lease_token=claim.lease_token
        )
        job_repo.close_attempt(
            session,
            job_id=claim.job_id,
            attempt_no=claim.attempt_no,
            outcome="succeeded",
        )


def _handle_sync_claim(
    session: Session,
    claim: ClaimedJob,
    clock: BusinessClock,
    crm: CrmPort,
    scheduler: Scheduler,
) -> None:
    # Block 1 (commit): fence, blocked check, recovery decision and
    # provider lookup. On NotFound the intent is inserted here, so
    # this commit makes it durable before any create is dispatched.
    lookup: crm_sync.SyncLookup | None = None
    contact: object = None
    unresolved_id: uuid.UUID | None = None
    blocked = False
    try:
        with session.begin():
            fenced = job_repo.fence_claim(
                session, claim.job_id, claim.lease_token
            )
            if fenced == 0:
                return
            if health.is_blocked(session):
                # Defence in depth: re-block without reaching the
                # provider at all while the provider is blocked.
                job_repo.close_attempt(
                    session,
                    job_id=claim.job_id,
                    attempt_no=claim.attempt_no,
                    outcome="blocked",
                    error_code="auth_failed",
                )
                job_repo.move_job_to_blocked(
                    session,
                    claim.job_id,
                    error_code="auth_failed",
                    lease_token=claim.lease_token,
                )
                blocked = True
            else:
                unresolved = crm_repo.get_unresolved_operation_by_job(
                    session, claim.job_id
                )
                if unresolved is not None:
                    unresolved_id = unresolved.id
                    contact = crm_sync.load_contact(session, claim)
                else:
                    lookup = crm_sync.begin_sync(session, claim, crm)
    except ProviderError as err:
        # The lookup itself failed: block 1 rolled back, no intent
        # was written, and the failure is recorded in a fresh block.
        if not _record_provider_failure(
            session,
            claim,
            clock,
            scheduler,
            err,
            operation_id=None,
            mark_operation_failed=False,
        ):
            raise
        return
    if blocked:
        return
    if unresolved_id is not None:
        # Recovery is decided by the unresolved operation itself,
        # regardless of the state the job was claimed from.
        _handle_readback(
            session, claim, clock, crm, contact, unresolved_id, scheduler
        )
        return
    assert lookup is not None
    if isinstance(lookup.found, FoundContact):
        found = lookup.found
        candidates = crm_sync.existing_patch_candidates(lookup.contact, found)
        try:
            crm_sync.perform_existing_patch(
                crm, lookup.context, found, candidates
            )
        except ProviderError as err:
            if not _record_provider_failure(
                session,
                claim,
                clock,
                scheduler,
                err,
                operation_id=None,
                mark_operation_failed=False,
            ):
                raise
            return
        # Block 3 (commit): preserved fields, link and synced event.
        with session.begin():
            if not _lease_still_held(session, claim):
                return
            crm_sync.record_existing_sync(
                session, claim, lookup.contact, found
            )
            job_repo.mark_job_succeeded(
                session, claim.job_id, lease_token=claim.lease_token
            )
            job_repo.close_attempt(
                session,
                job_id=claim.job_id,
                attempt_no=claim.attempt_no,
                outcome="succeeded",
            )
        return
    # Step 2 (no open transaction): dispatch the create. The intent
    # committed in block 1 survives any failure here, including a
    # BaseException simulating process death.
    assert lookup.operation_id is not None
    try:
        created = crm_sync.create_remote_contact(
            crm, lookup.contact, lookup.context
        )
    except ProviderError as err:
        # Block 4 (commit): record the failure state in a fresh
        # transaction the provider exception did not roll back.
        if not _record_provider_failure(
            session,
            claim,
            clock,
            scheduler,
            err,
            operation_id=lookup.operation_id,
            mark_operation_failed=True,
        ):
            raise
        return
    # Block 3 (commit): create outcome, link and synced event.
    with session.begin():
        if not _lease_still_held(session, claim):
            return
        crm_sync.record_create_success(
            session, claim, lookup.contact, lookup.operation_id, created
        )
        job_repo.mark_job_succeeded(
            session, claim.job_id, lease_token=claim.lease_token
        )
        job_repo.close_attempt(
            session,
            job_id=claim.job_id,
            attempt_no=claim.attempt_no,
            outcome="succeeded",
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
    the lease is stale — return quietly, write nothing. A sync claim
    runs in sequentially committed blocks (see _handle_sync_claim)
    so the operation intent survives a crash after the remote
    create; the prepare path keeps its single transaction.
    """
    if claim.kind == "sync_contact":
        if crm is None:
            raise ValueError("sync_contact requires a CRM port")
        _handle_sync_claim(
            session, claim, clock, crm, scheduler or BackoffScheduler()
        )
        return
    with session.begin():
        fenced = job_repo.fence_claim(session, claim.job_id, claim.lease_token)
        if fenced == 0:
            return
        if claim.kind == "prepare_enquiry":
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
        job_repo.mark_job_succeeded(
            session, claim.job_id, lease_token=claim.lease_token
        )
        job_repo.close_attempt(
            session,
            job_id=claim.job_id,
            attempt_no=claim.attempt_no,
            outcome="succeeded",
        )
