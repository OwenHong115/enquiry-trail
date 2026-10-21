"""S4b red tests: worker claim, lease, fencing and local prepare.

Source contracts (transcribed by hand; plan package v3.0):
- Job transitions pending->running (claim: set lease, increment
  attempt) and running->succeeded, plus claim/lease rules, come
  from the behaviour contract's job_transitions and worker sections.
- Event shapes (enquiry.prepared, job.lease_recovered) come from
  the vocabulary contract. No sleeps anywhere: due times and lease
  expiry are arranged by writing timestamps with SQL.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.intake import IntakeResult, submit_intake
from app.worker.claiming import ClaimedJob, claim_next, handle_claim
from support.fake_clock import FakeClock
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WORKER_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _payload(tag: int, **overrides: object) -> dict:
    payload: dict = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": f"worker{tag}@example.com",
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": f"Renovation enquiry number {tag} with enough detail.",
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }
    payload.update(overrides)
    return payload


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _submit(postgres_db: str, tag: int, clock: FakeClock) -> IntakeResult:
    session = _factory(postgres_db)()
    try:
        return submit_intake(
            session,
            _payload(tag),
            source=SOURCE,
            event_key=uuid.uuid4(),
            clock=clock,
            routing_config=ROUTING_CONFIG,
        )
    finally:
        session.close()


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


def _job_row(postgres_db: str, job_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT kind, entity_id, state, lease_token, lease_until, "
        "attempt_count, cycle_attempt_count, heartbeat_at "
        "FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0]


def _prepare_job_id(postgres_db: str, enquiry_id: uuid.UUID) -> uuid.UUID:
    return _fetch(
        postgres_db,
        "SELECT id FROM leadops.jobs WHERE entity_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0].id


def _claim(postgres_db: str, worker_id: uuid.UUID, clock: FakeClock):
    session = _factory(postgres_db)()
    try:
        return claim_next(session, worker_id, clock)
    finally:
        session.close()


def test_claim_due_job_stamps_lease_and_attempt(postgres_db: str) -> None:
    clock = FakeClock(T0)
    result = _submit(postgres_db, 1, clock)
    job_id = _prepare_job_id(postgres_db, result.enquiry_id)

    claim = _claim(postgres_db, WORKER_A, clock)

    assert claim is not None
    assert claim.job_id == job_id
    assert claim.kind == "prepare_enquiry"
    assert claim.entity_id == result.enquiry_id
    assert claim.attempt_no == 1

    job = _job_row(postgres_db, job_id)
    # Contract: claim moves pending->running, sets the lease and
    # increments both attempt counters, and stamps the heartbeat.
    assert job.state == "running"
    assert job.lease_token == claim.lease_token
    assert job.lease_until is not None
    assert job.attempt_count == 1
    assert job.cycle_attempt_count == 1
    assert job.heartbeat_at is not None

    attempts = _fetch(
        postgres_db,
        "SELECT attempt_no, lease_token, finished_at FROM leadops.job_attempts "
        "WHERE job_id = :job_id",
        {"job_id": job_id},
    )
    assert len(attempts) == 1
    assert attempts[0].attempt_no == 1
    assert attempts[0].lease_token == claim.lease_token
    assert attempts[0].finished_at is None


def test_due_discipline_and_selection_order(postgres_db: str) -> None:
    clock = FakeClock(T0)
    first = _submit(postgres_db, 1, clock)
    first_job = _prepare_job_id(postgres_db, first.enquiry_id)

    # Not yet due at T0: no claim, job untouched.
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET next_attempt_at = :when WHERE id = :job_id",
        {"when": T0 + timedelta(seconds=60), "job_id": first_job},
    )
    assert _claim(postgres_db, WORKER_A, clock) is None
    assert _job_row(postgres_db, first_job).state == "pending"

    # Two due jobs: the earlier next_attempt_at wins.
    second = _submit(postgres_db, 2, clock)
    second_job = _prepare_job_id(postgres_db, second.enquiry_id)
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET next_attempt_at = :when WHERE id = :job_id",
        {"when": T0 + timedelta(seconds=30), "job_id": first_job},
    )
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET next_attempt_at = :when WHERE id = :job_id",
        {"when": T0 + timedelta(seconds=10), "job_id": second_job},
    )
    clock.advance_to(T0 + timedelta(seconds=15))
    claim = _claim(postgres_db, WORKER_A, clock)
    assert claim is not None
    assert claim.job_id == second_job

    # Equal instants fall back to created_at, then id.
    third = _submit(postgres_db, 3, clock)
    third_job = _prepare_job_id(postgres_db, third.enquiry_id)
    fourth = _submit(postgres_db, 4, clock)
    fourth_job = _prepare_job_id(postgres_db, fourth.enquiry_id)
    for job_id, created in (
        (third_job, T0 + timedelta(seconds=5)),
        (fourth_job, T0 + timedelta(seconds=1)),
    ):
        _execute(
            postgres_db,
            "UPDATE leadops.jobs SET next_attempt_at = :due, "
            "created_at = :created WHERE id = :job_id",
            {"due": T0, "created": created, "job_id": job_id},
        )
    # first_job (due T0+30) is also due by ordering rules only after
    # the two T0 jobs; claim the two T0 jobs in created_at order.
    claim = _claim(postgres_db, WORKER_A, clock)
    assert claim is not None
    assert claim.job_id == fourth_job
    claim = _claim(postgres_db, WORKER_A, clock)
    assert claim is not None
    assert claim.job_id == third_job


def test_concurrent_claims_are_disjoint(postgres_db: str) -> None:
    clock = FakeClock(T0)
    results = [_submit(postgres_db, tag, clock) for tag in range(1, 5)]
    expected_jobs = {
        _prepare_job_id(postgres_db, result.enquiry_id) for result in results
    }

    factory = _factory(postgres_db)
    barrier = threading.Barrier(4)
    claims: list[ClaimedJob] = []
    errors: list[BaseException] = []

    def worker(worker_id: uuid.UUID) -> None:
        session = factory()
        try:
            barrier.wait(timeout=15)
            claim = claim_next(session, worker_id, clock)
            if claim is not None:
                claims.append(claim)
        except BaseException as exc:  # collected and asserted below
            errors.append(exc)
        finally:
            session.close()

    worker_ids = [uuid.uuid4() for _ in range(4)]
    threads = [
        threading.Thread(target=worker, args=(worker_id,))
        for worker_id in worker_ids
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    # Contract: FOR UPDATE SKIP LOCKED claiming hands each due job
    # to exactly one worker; four jobs means four successful claims.
    assert errors == [], f"unexpected claim errors: {errors!r}"
    assert len(claims) == 4
    assert {claim.job_id for claim in claims} == expected_jobs
    attempts = _fetch(
        postgres_db, "SELECT job_id FROM leadops.job_attempts"
    )
    assert len(attempts) == 4
    assert {row.job_id for row in attempts} == expected_jobs


def test_prepare_marks_enquiry_prepared(postgres_db: str) -> None:
    clock = FakeClock(T0)
    result = _submit(postgres_db, 1, clock)
    job_id = _prepare_job_id(postgres_db, result.enquiry_id)
    claim = _claim(postgres_db, WORKER_A, clock)
    assert claim is not None

    session = _factory(postgres_db)()
    try:
        handle_claim(session, claim, clock)
    finally:
        session.close()

    enquiry = _fetch(
        postgres_db,
        "SELECT local_prepared_at, owner_id FROM leadops.enquiries "
        "WHERE id = :enquiry_id",
        {"enquiry_id": result.enquiry_id},
    )[0]
    # Contract: prepare stamps local_prepared_at from the
    # BusinessClock and the job ends succeeded.
    assert enquiry.local_prepared_at == T0
    assert _job_row(postgres_db, job_id).state == "succeeded"

    events = _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = 'enquiry.prepared'",
    )
    assert len(events) == 1
    assert events[0].details_json["owner_id"] == str(enquiry.owner_id)
    assert events[0].details_json["routing_reason"] == "west_renovation"

    attempts = _fetch(
        postgres_db,
        "SELECT finished_at, outcome FROM leadops.job_attempts "
        "WHERE job_id = :job_id",
        {"job_id": job_id},
    )
    assert len(attempts) == 1
    assert attempts[0].finished_at is not None
    assert attempts[0].outcome

    tasks = _fetch(
        postgres_db, "SELECT count(*) AS n FROM leadops.follow_up_tasks"
    )[0].n
    assert tasks == 1
    received = _fetch(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.activity_events "
        "WHERE event_type = 'enquiry.received'",
    )[0].n
    assert received == 1


def _claim_expire_reclaim(postgres_db: str, clock: FakeClock):
    result = _submit(postgres_db, 1, clock)
    job_id = _prepare_job_id(postgres_db, result.enquiry_id)
    first_claim = _claim(postgres_db, WORKER_A, clock)
    assert first_claim is not None
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET lease_until = :when WHERE id = :job_id",
        {"when": T0 - timedelta(seconds=1), "job_id": job_id},
    )
    second_claim = _claim(postgres_db, WORKER_B, clock)
    return result, job_id, first_claim, second_claim


def test_lease_expiry_recovery(postgres_db: str) -> None:
    clock = FakeClock(T0)
    _, job_id, first_claim, second_claim = _claim_expire_reclaim(
        postgres_db, clock
    )

    assert second_claim is not None
    assert second_claim.job_id == job_id
    # Contract: an expired lease is recovered by a fresh claim with
    # a new token; attempt counters keep counting up.
    assert second_claim.lease_token != first_claim.lease_token
    assert second_claim.attempt_no == 2
    job = _job_row(postgres_db, job_id)
    assert job.state == "running"
    assert job.lease_token == second_claim.lease_token
    assert job.attempt_count == 2

    events = _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = 'job.lease_recovered'",
    )
    assert len(events) == 1
    assert events[0].details_json["expired_lease_token"] == str(
        first_claim.lease_token
    )
    attempts = _fetch(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.job_attempts WHERE job_id = :job_id",
        {"job_id": job_id},
    )[0].n
    assert attempts == 2


def test_stale_lease_token_is_fenced(postgres_db: str) -> None:
    clock = FakeClock(T0)
    result, job_id, first_claim, second_claim = _claim_expire_reclaim(
        postgres_db, clock
    )
    assert second_claim is not None

    # The stale claim writes nothing: the fence is the lease token
    # in the write's WHERE clause, not timing.
    session = _factory(postgres_db)()
    try:
        handle_claim(session, first_claim, clock)
    finally:
        session.close()

    enquiry = _fetch(
        postgres_db,
        "SELECT local_prepared_at FROM leadops.enquiries WHERE id = :enquiry_id",
        {"enquiry_id": result.enquiry_id},
    )[0]
    assert enquiry.local_prepared_at is None
    assert (
        _fetch(
            postgres_db,
            "SELECT count(*) AS n FROM leadops.activity_events "
            "WHERE event_type = 'enquiry.prepared'",
        )[0].n
        == 0
    )
    job = _job_row(postgres_db, job_id)
    assert job.state == "running"
    assert job.lease_token == second_claim.lease_token

    # The current claim then handles normally.
    session = _factory(postgres_db)()
    try:
        handle_claim(session, second_claim, clock)
    finally:
        session.close()
    enquiry = _fetch(
        postgres_db,
        "SELECT local_prepared_at FROM leadops.enquiries WHERE id = :enquiry_id",
        {"enquiry_id": result.enquiry_id},
    )[0]
    assert enquiry.local_prepared_at == T0
    assert _job_row(postgres_db, job_id).state == "succeeded"
