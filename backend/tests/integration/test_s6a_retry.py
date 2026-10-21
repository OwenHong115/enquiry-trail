"""S6a red tests: A11 retry backoff and A17 Retry-After.

Source contracts (transcribed by hand; plan package v3.0):
- Retry scheduling (backoff 1/4/16, retryable-before-write moves
  to waiting_retry, job.retry_scheduled with job_id /
  next_attempt_at / attempt_no) comes from the behaviour
  contract's worker and job_transitions sections.
- Retry-After defers the next attempt to exactly that instant;
  eligibility is asserted by claiming around it with the fake
  clock, never by sleeping.

Only connect_failed, unavailable and rate_limited are retryable
in this slice. Everything else stays unhandled here by design.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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
WORKER = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
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


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _submit_and_enqueue_sync(postgres_db: str, clock: FakeClock) -> uuid.UUID:
    session = _factory(postgres_db)()
    try:
        result = submit_intake(
            session,
            PAYLOAD,
            source=SOURCE,
            event_key=uuid.uuid4(),
            clock=clock,
            routing_config=ROUTING_CONFIG,
        )
        enquiry_id = result.enquiry_id
    finally:
        session.close()

    session = _factory(postgres_db)()
    try:
        prepare = claim_next(session, WORKER, clock)
        assert prepare is not None
        handle_claim(session, prepare, clock)
    finally:
        session.close()

    sync_job = _fetch(
        postgres_db,
        "SELECT id FROM leadops.jobs WHERE kind = 'sync_contact'",
    )
    assert len(sync_job) == 1
    return sync_job[0].id


def _claim(postgres_db: str, clock: FakeClock):
    session = _factory(postgres_db)()
    try:
        return claim_next(session, WORKER, clock)
    finally:
        session.close()


def _handle_sync(postgres_db: str, claim, clock: FakeClock, fake: FakeCrm) -> None:
    session = _factory(postgres_db)()
    try:
        handle_claim(
            session, claim, clock, crm=fake, scheduler=FakeScheduler()
        )
    finally:
        session.close()


def _job(postgres_db: str, job_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT state, next_attempt_at FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0]


def _attempts(postgres_db: str, job_id: uuid.UUID) -> list:
    return _fetch(
        postgres_db,
        "SELECT attempt_no, error_code, finished_at FROM leadops.job_attempts "
        "WHERE job_id = :job_id ORDER BY attempt_no",
        {"job_id": job_id},
    )


def _retry_events(postgres_db: str) -> list:
    return _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = 'job.retry_scheduled' ORDER BY created_at, id",
    )


def _as_instant(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def test_a11_retryable_failures_backoff_then_succeed(postgres_db: str) -> None:
    clock = FakeClock(T0)
    job_id = _submit_and_enqueue_sync(postgres_db, clock)
    fake = FakeCrm(
        lookup_script=[
            ProviderError("unavailable", http_status=503),
            ProviderError("unavailable", http_status=503),
            NotFound(),
        ]
    )

    # Attempt 1 at T0: retryable failure before any write.
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle_sync(postgres_db, claim, clock, fake)

    job = _job(postgres_db, job_id)
    # Contract A11: waiting_retry with the first backoff step (1s).
    assert job.state == "waiting_retry"
    assert job.next_attempt_at == T0 + timedelta(seconds=1)
    attempts = _attempts(postgres_db, job_id)
    # The prepare attempt belongs to a different job; this counts
    # only the sync job's own attempt rows.
    assert len(attempts) == 1
    assert attempts[0].error_code == "unavailable"
    assert attempts[0].finished_at is not None
    events = _retry_events(postgres_db)
    assert len(events) == 1
    assert events[0].details_json["job_id"] == str(job_id)
    assert events[0].details_json["attempt_no"] == 1
    assert _as_instant(events[0].details_json["next_attempt_at"]) == (
        T0 + timedelta(seconds=1)
    )

    # Not yet due: no claim half a second later.
    clock.advance_to(T0 + timedelta(seconds=0.5))
    assert _claim(postgres_db, clock) is None

    # Attempt 2 at T0+1: second backoff step lands at T0+5.
    clock.advance_to(T0 + timedelta(seconds=1))
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle_sync(postgres_db, claim, clock, fake)

    job = _job(postgres_db, job_id)
    assert job.state == "waiting_retry"
    assert job.next_attempt_at == T0 + timedelta(seconds=5)
    assert len(_retry_events(postgres_db)) == 2
    assert _retry_events(postgres_db)[1].details_json["attempt_no"] == 2

    # Attempt 3 at T0+5: lookup now succeeds via create.
    clock.advance_to(T0 + timedelta(seconds=5))
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle_sync(postgres_db, claim, clock, fake)

    assert _job(postgres_db, job_id).state == "succeeded"
    assert len(fake.calls_of("create_contact")) == 1
    links = _fetch(postgres_db, "SELECT id FROM leadops.crm_links")
    assert len(links) == 1
    synced = _fetch(
        postgres_db,
        "SELECT id FROM leadops.activity_events "
        "WHERE event_type = 'crm.synced'",
    )
    assert len(synced) == 1
    tasks = _fetch(
        postgres_db, "SELECT count(*) AS n FROM leadops.follow_up_tasks"
    )[0].n
    assert tasks == 1


def test_a17_retry_after_defers_next_attempt_exactly(postgres_db: str) -> None:
    clock = FakeClock(T0)
    job_id = _submit_and_enqueue_sync(postgres_db, clock)
    fake = FakeCrm(
        lookup_script=[
            ProviderError(
                "rate_limited", http_status=429, retry_after_seconds=2
            ),
            NotFound(),
        ]
    )

    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle_sync(postgres_db, claim, clock, fake)

    job = _job(postgres_db, job_id)
    # Contract A17: Retry-After wins over backoff, exactly — the
    # next attempt is due at T0+2, not at the 1s backoff step.
    assert job.state == "waiting_retry"
    assert job.next_attempt_at == T0 + timedelta(seconds=2)

    clock.advance_to(T0 + timedelta(seconds=1.999))
    assert _claim(postgres_db, clock) is None

    clock.advance_to(T0 + timedelta(seconds=2))
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_id
