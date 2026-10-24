"""R9 red tests: close the per-cycle budget at claim time.

The per-cycle budget is enforced only inside the retryable-failure
branch of _record_provider_failure. Two paths still push
cycle_attempt_count past the schema ceiling
(CHECK (cycle_attempt_count BETWEEN 0 AND 4),
alembic/versions/0001_initial.py:232):

1. Crash during the fourth attempt: the job stays "running", the
   lease expires, and the next claim_next stamps a lease,
   incrementing the count to 5. The CHECK raises IntegrityError
   from inside claim_next, before any work happens, and because
   the transaction rolls back the poisoned job stays claimable:
   every later claim raises again and the worker loop dies.
2. A readback loop that keeps returning NotFound:
   _handle_readback reschedules unconditionally, so readbacks also
   consume the budget until the fifth claim hits the same CHECK.

Counting rule these tests assume (the simplest coherent one):
every claim consumes one unit of the cycle budget whether it
dispatches a create or a readback.

No sleeps: lease expiry is arranged with SQL and every due
instant with the fake clock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import NotFound, ProviderError
from app.services.intake import submit_intake
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")

# The ProviderError vocabulary (app/ports/crm.py): a claim-time
# closure must record one of these, never an invented code.
ALLOWED_ERROR_CODES = frozenset(
    {
        "connect_failed",
        "rate_limited",
        "unavailable",
        "auth_failed",
        "validation_failed",
        "not_found",
        "conflict",
        "result_unknown",
        "invalid_response",
        "director_unavailable",
    }
)


def _payload(tag: int) -> dict:
    return {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": f"budget{tag}@example.com",
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": f"Renovation enquiry number {tag} with enough detail.",
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _execute(postgres_db: str, sql: str, params: dict | None = None) -> None:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


def _submit(postgres_db: str, tag: int, clock: FakeClock) -> uuid.UUID:
    session = _factory(postgres_db)()
    try:
        result = submit_intake(
            session,
            _payload(tag),
            source=SOURCE,
            event_key=uuid.uuid4(),
            clock=clock,
            routing_config=ROUTING_CONFIG,
        )
        return result.enquiry_id
    finally:
        session.close()


def _claim(postgres_db: str, clock: FakeClock):
    session = _factory(postgres_db)()
    try:
        return claim_next(session, WORKER, clock)
    finally:
        session.close()


def _handle(postgres_db: str, claim, clock: FakeClock, fake: FakeCrm) -> None:
    session = _factory(postgres_db)()
    try:
        handle_claim(session, claim, clock, crm=fake, scheduler=FakeScheduler())
    finally:
        session.close()


def _drain_prepare(postgres_db: str, clock: FakeClock) -> None:
    prepare = _claim(postgres_db, clock)
    assert prepare is not None
    assert prepare.kind == "prepare_enquiry"
    _handle(postgres_db, prepare, clock, FakeCrm())


def _sync_job_id(postgres_db: str, enquiry_id: uuid.UUID) -> uuid.UUID:
    return _fetch(
        postgres_db,
        "SELECT id FROM leadops.jobs WHERE kind = 'sync_contact' "
        "AND entity_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0].id


def _job_row(postgres_db: str, job_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT state, cycle_attempt_count, last_error_code, "
        "next_attempt_at FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0]


def _expire_lease(postgres_db: str, job_id: uuid.UUID) -> None:
    _execute(
        postgres_db,
        "UPDATE leadops.jobs "
        "SET lease_until = now() - interval '1 second' "
        "WHERE id = :job_id",
        {"job_id": job_id},
    )


def _attempt_rows(postgres_db: str, job_id: uuid.UUID) -> int:
    return _fetch(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.job_attempts "
        "WHERE job_id = :job_id",
        {"job_id": job_id},
    )[0].n


def _failed_events(postgres_db: str) -> int:
    return _fetch(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.activity_events "
        "WHERE event_type = 'job.failed'",
    )[0].n


def test_job_at_budget_ceiling_is_closed_at_claim_time(
    postgres_db: str,
) -> None:
    clock = FakeClock(T0)
    enquiry_id = _submit(postgres_db, 1, clock)
    _drain_prepare(postgres_db, clock)
    sync_job_id = _sync_job_id(postgres_db, enquiry_id)

    # Four crashed attempts: each claim stamps a lease (consuming
    # one budget unit), then the provider call dies with a raw
    # BaseException before any finalization can run.
    for attempt in range(4):
        claim = _claim(postgres_db, clock)
        assert claim is not None
        assert claim.job_id == sync_job_id
        session = _factory(postgres_db)()
        try:
            with pytest.raises(BaseException):
                handle_claim(
                    session,
                    claim,
                    clock,
                    crm=FakeCrm(error=BaseException("crash")),
                    scheduler=FakeScheduler(),
                )
        finally:
            session.close()
        if attempt < 3:
            _expire_lease(postgres_db, sync_job_id)

    # The poisoned setup: at the ceiling, still running, 4 attempts.
    poisoned = _job_row(postgres_db, sync_job_id)
    assert poisoned.cycle_attempt_count == 4
    assert poisoned.state == "running"
    assert _attempt_rows(postgres_db, sync_job_id) == 4

    # A second due job exists. The poisoned job is ordered first
    # (earlier next_attempt_at), so the next claim meets it first:
    # green must close it and keep scanning within the same call.
    second_enquiry = _submit(postgres_db, 2, clock)
    _drain_prepare(postgres_db, clock)
    second_job_id = _sync_job_id(postgres_db, second_enquiry)
    # Only now make the poisoned job claimable again, ordered
    # ahead of the second job.
    _expire_lease(postgres_db, sync_job_id)
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET next_attempt_at = :when "
        "WHERE id = :job_id",
        {"when": T0 - timedelta(seconds=60), "job_id": sync_job_id},
    )

    # The intended red: today this raises IntegrityError from
    # stamp_lease (cycle_attempt_count 4 -> 5 breaks the CHECK).
    claim = _claim(postgres_db, clock)

    assert claim is not None
    assert claim.job_id == second_job_id

    closed = _job_row(postgres_db, sync_job_id)
    assert closed.state == "dead"
    assert closed.last_error_code in ALLOWED_ERROR_CODES
    assert _failed_events(postgres_db) == 1
    assert _attempt_rows(postgres_db, sync_job_id) == 4


def test_readback_loop_exhausts_budget_without_wedging_queue(
    postgres_db: str,
) -> None:
    clock = FakeClock(T0)
    enquiry_id = _submit(postgres_db, 1, clock)
    _drain_prepare(postgres_db, clock)
    sync_job_id = _sync_job_id(postgres_db, enquiry_id)

    # Drive the create to an unknown result: reconciling, with an
    # unresolved operation, one budget unit consumed.
    claim = _claim(postgres_db, clock)
    assert claim is not None
    _handle(
        postgres_db,
        claim,
        clock,
        FakeCrm(
            lookup_script=[NotFound()],
            create_commit_then_error=ProviderError("result_unknown"),
        ),
    )
    job = _job_row(postgres_db, sync_job_id)
    assert job.state == "reconciling"
    assert job.cycle_attempt_count == 1

    # Every readback returns NotFound. Each claim consumes one
    # budget unit, so the ceiling arrives after three readbacks;
    # the job must close terminally instead of wedging the queue.
    # The intended red: today the fifth claim raises IntegrityError
    # from inside claim_next, so this loop never finishes.
    readback_fake = FakeCrm()
    for _ in range(6):
        job = _job_row(postgres_db, sync_job_id)
        if job.state in ("dead", "blocked"):
            break
        clock.advance_to(job.next_attempt_at)
        claim = _claim(postgres_db, clock)
        if claim is None:
            # The claim-time closure closed the job at the ceiling
            # and nothing else was due yet: that is exactly the
            # termination this loop is driving towards.
            assert _job_row(postgres_db, sync_job_id).state == "dead"
            break
        assert claim.job_id == sync_job_id
        _handle(postgres_db, claim, clock, readback_fake)

    job = _job_row(postgres_db, sync_job_id)
    assert job.state == "dead"
    assert _failed_events(postgres_db) == 1

    # The unresolved operation is preserved for a future operator
    # retry: still unknown, not reconciled and not failed.
    operations = _fetch(
        postgres_db,
        "SELECT status FROM leadops.external_operations "
        "WHERE job_id = :job_id",
        {"job_id": sync_job_id},
    )
    assert len(operations) == 1
    assert operations[0].status == "unknown"

    # The queue still serves another due job, and claiming it
    # dispatches no further readback for the dead job.
    second_enquiry = _submit(postgres_db, 2, clock)
    _drain_prepare(postgres_db, clock)
    second_job_id = _sync_job_id(postgres_db, second_enquiry)
    lookups_before = len(readback_fake.calls_of("get_contact_by_email"))
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == second_job_id
    assert len(readback_fake.calls_of("get_contact_by_email")) == lookups_before
