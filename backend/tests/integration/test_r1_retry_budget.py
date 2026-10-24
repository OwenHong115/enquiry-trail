"""R1 red tests: the per-cycle retry budget (A18, first half).

Source contracts (transcribed by hand; plan package v3.0):
- behavior.yaml#worker: per_cycle_max_attempts 4, retry_seconds
  1/4/16, jitter_max_seconds 0.5.
- behavior.yaml job_transitions: running -> dead on
  permanent_error_or_budget, effect emit_failed.
- vocabulary job.failed: actor worker, severity error, details
  job_id and error_code (a ProviderError code).

Operator manual retry (dead -> pending, new cycle) is A18's second
half and is out of scope here; the new-cycle arrangement in the
third test is done with SQL, as the slice brief allows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import ProviderError
from app.services.intake import submit_intake
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
PAYLOAD: dict = {
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": "alex.demo@example.com",
    "phone": "+61412345678",
    "postcode": "2150",
    "service_type": "bathroom",
    "message": "I need a bathroom renovation next month. My budget is around AUD 20,000.",
    "preferred_contact": "email",
    "acknowledge_demo": True,
}
SECOND_PAYLOAD: dict = {
    **PAYLOAD,
    "first_name": "Sam",
    "last_name": "Lee",
    "email": "sam.lee@example.com",
    "message": "Kitchen quote please, ideally this quarter.",
    "service_type": "kitchen",
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


def _submit(postgres_db: str, clock: FakeClock, payload: dict) -> uuid.UUID:
    session = _factory(postgres_db)()
    try:
        result = submit_intake(
            session,
            payload,
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


def _enqueue_sync_jobs(postgres_db: str, clock: FakeClock, count: int) -> list:
    """Submit `count` enquiries and drain their prepare jobs.

    Returns the sync job ids in submission order.
    """
    payloads = [PAYLOAD, SECOND_PAYLOAD][:count]
    enquiry_ids = [_submit(postgres_db, clock, p) for p in payloads]
    for _ in enquiry_ids:
        session = _factory(postgres_db)()
        try:
            prepare = claim_next(session, WORKER, clock)
            assert prepare is not None and prepare.kind == "prepare_enquiry"
            handle_claim(session, prepare, clock)
        finally:
            session.close()
    jobs = _fetch(
        postgres_db,
        "SELECT id, entity_id FROM leadops.jobs WHERE kind = 'sync_contact' "
        "ORDER BY created_at, id",
    )
    assert len(jobs) == count
    by_entity = {row.entity_id: row.id for row in jobs}
    return [by_entity[enquiry_id] for enquiry_id in enquiry_ids]


def _job(postgres_db: str, job_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT state, next_attempt_at, last_error_code, attempt_count, "
        "cycle_no, cycle_attempt_count FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0]


def _attempts(postgres_db: str, job_id: uuid.UUID) -> list:
    return _fetch(
        postgres_db,
        "SELECT attempt_no, error_code, finished_at, outcome "
        "FROM leadops.job_attempts WHERE job_id = :job_id "
        "ORDER BY attempt_no",
        {"job_id": job_id},
    )


def _events(postgres_db: str, event_type: str) -> list:
    return _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = :event_type ORDER BY created_at, id",
        {"event_type": event_type},
    )


def _failing_fake(failures: int) -> FakeCrm:
    return FakeCrm(
        lookup_script=[
            ProviderError("unavailable", http_status=503)
            for _ in range(failures)
        ]
    )


def _fail_once(postgres_db: str, clock: FakeClock, job_id: uuid.UUID, fake) -> None:
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)


def _drive_to_dead(postgres_db: str, clock: FakeClock, job_id: uuid.UUID) -> None:
    """Four retryable failures at T0, T0+1, T0+5, T0+21 (1/4/16)."""
    fake = _failing_fake(4)
    for offset in (0, 1, 5, 21):
        clock.advance_to(T0 + timedelta(seconds=offset))
        _fail_once(postgres_db, clock, job_id, fake)
    assert _job(postgres_db, job_id).state == "waiting_retry"
    assert _claim(postgres_db, clock) is None


def test_fourth_retryable_failure_in_cycle_is_dead(postgres_db: str) -> None:
    clock = FakeClock(T0)
    (job_id,) = _enqueue_sync_jobs(postgres_db, clock, 1)
    fake = _failing_fake(4)

    # Attempts 1-3 schedule waiting_retry at the 1/4/16 steps.
    for attempt_no, (at, due) in enumerate(
        [(0, 1), (1, 5), (5, 21)], start=1
    ):
        clock.advance_to(T0 + timedelta(seconds=at))
        _fail_once(postgres_db, clock, job_id, fake)
        job = _job(postgres_db, job_id)
        assert job.state == "waiting_retry"
        assert job.next_attempt_at == T0 + timedelta(seconds=due)

    # Attempt 4 records its failure; only the next claim owns closure.
    # The budget closure must not wait for the scheduled retry instant.
    clock.advance_to(T0 + timedelta(seconds=21))
    _fail_once(postgres_db, clock, job_id, fake)
    assert _job(postgres_db, job_id).state == "waiting_retry"
    assert _claim(postgres_db, clock) is None

    job = _job(postgres_db, job_id)
    assert job.state == "dead"
    assert job.last_error_code == "unavailable"

    attempts = _attempts(postgres_db, job_id)
    assert len(attempts) == 4
    assert attempts[-1].error_code == "unavailable"
    assert attempts[-1].finished_at is not None
    assert attempts[-1].outcome is not None

    # Exactly one job.failed, carrying the job and the provider
    # error code. Four failures were scheduled, but only four attempts ran.
    failed = _events(postgres_db, "job.failed")
    assert len(failed) == 1
    assert failed[0].details_json["job_id"] == str(job_id)
    assert failed[0].details_json["error_code"] == "unavailable"
    assert len(_events(postgres_db, "job.retry_scheduled")) == 4


def test_dead_job_is_not_claimed_and_does_not_wedge_queue(
    postgres_db: str,
) -> None:
    clock = FakeClock(T0)
    dead_job_id, second_job_id = _enqueue_sync_jobs(postgres_db, clock, 2)

    # Park the second job far in the future so every claim while
    # driving the first job to death can only pick the first.
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET next_attempt_at = :due WHERE id = :job_id",
        {"due": T0 + timedelta(hours=1), "job_id": second_job_id},
    )
    _drive_to_dead(postgres_db, clock, dead_job_id)
    assert _job(postgres_db, dead_job_id).state == "dead"

    # A dead job is excluded from claiming: with nothing else due,
    # the claim is empty rather than the dead job again.
    clock.advance_to(T0 + timedelta(seconds=22))
    assert _claim(postgres_db, clock) is None

    # The queue is not wedged: once the second job is due, it is
    # claimed normally.
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET next_attempt_at = :due WHERE id = :job_id",
        {"due": T0 + timedelta(seconds=22), "job_id": second_job_id},
    )
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == second_job_id


def test_backoff_uses_cycle_attempt_count_not_lifetime(postgres_db: str) -> None:
    clock = FakeClock(T0)
    (job_id,) = _enqueue_sync_jobs(postgres_db, clock, 1)
    fake = _failing_fake(4)

    # Three failures in the first cycle: lifetime attempts 1-3,
    # backoff steps 1/4/16.
    for at in (0, 1, 5):
        clock.advance_to(T0 + timedelta(seconds=at))
        _fail_once(postgres_db, clock, job_id, fake)
    before = _job(postgres_db, job_id)
    assert before.attempt_count == 3
    assert before.cycle_attempt_count == 3

    # A new cycle starts (operator retry / resume semantics,
    # arranged by SQL as the brief allows): cycle_no + 1, the cycle
    # counter reset, the job due now. The lifetime count keeps its
    # history at 3, so the next claim is lifetime attempt 4.
    clock.advance_to(T0 + timedelta(seconds=21))
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET state = 'pending', "
        "cycle_no = cycle_no + 1, cycle_attempt_count = 0, "
        "next_attempt_at = :due, lease_token = NULL, "
        "lease_until = NULL, heartbeat_at = NULL WHERE id = :job_id",
        {"due": T0 + timedelta(seconds=21), "job_id": job_id},
    )

    _fail_once(postgres_db, clock, job_id, fake)

    job = _job(postgres_db, job_id)
    # First failure of the new cycle: the 1s step, not the 16s step
    # the lifetime attempt number 4 would produce, and not dead.
    assert job.state == "waiting_retry"
    assert job.next_attempt_at == T0 + timedelta(seconds=22)
    assert job.cycle_no == before.cycle_no + 1
    assert job.cycle_attempt_count == 1
    assert job.attempt_count == 4


def test_fourth_attempt_can_still_succeed(postgres_db: str) -> None:
    clock = FakeClock(T0)
    (job_id,) = _enqueue_sync_jobs(postgres_db, clock, 1)
    failures = _failing_fake(3)
    for at in (0, 1, 5):
        clock.advance_to(T0 + timedelta(seconds=at))
        _fail_once(postgres_db, clock, job_id, failures)
    clock.advance_to(T0 + timedelta(seconds=21))
    _fail_once(postgres_db, clock, job_id, FakeCrm())
    assert _job(postgres_db, job_id).state == "succeeded"
    assert len(_attempts(postgres_db, job_id)) == 4
    assert _events(postgres_db, "job.failed") == []
