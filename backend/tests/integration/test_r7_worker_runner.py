"""R7b red test: there is no worker entrypoint.

app/worker/ contains only claiming.py: claim_next and handle_claim
are library functions driven exclusively by tests. Nothing claims
and handles jobs in a loop, so the pipeline cannot run as a process.

Pin: module app/worker/runner.py with

    def run_once(session: Session, worker_id: uuid.UUID,
                 clock: BusinessClock, crm: CrmPort,
                 scheduler: Scheduler | None = None) -> bool

It claims at most one due job with claim_next, handles it with
handle_claim, and returns True when a job was handled and False when
nothing was due.

The import below is at module top on purpose: today it fails with a
clear ImportError naming app.worker.runner, which is the intended
red for this file.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.intake import IntakeResult, submit_intake
from app.worker.runner import run_once
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
MAX_ITERATIONS = 10


def _payload(tag: int) -> dict:
    return {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": f"runner{tag}@example.com",
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": f"Renovation enquiry number {tag} with enough detail.",
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }


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


def _succeeded_count(postgres_db: str) -> int:
    return _fetch(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.jobs WHERE state = 'succeeded'",
    )[0].n


def _snapshot(postgres_db: str) -> tuple:
    jobs = _fetch(
        postgres_db,
        "SELECT id, kind, state FROM leadops.jobs ORDER BY id",
    )
    attempts = _fetch(
        postgres_db, "SELECT count(*) AS n FROM leadops.job_attempts"
    )[0].n
    events = _fetch(
        postgres_db, "SELECT count(*) AS n FROM leadops.activity_events"
    )[0].n
    prepared = _fetch(
        postgres_db,
        "SELECT id, local_prepared_at FROM leadops.enquiries ORDER BY id",
    )
    return (
        [(row.id, row.kind, row.state) for row in jobs],
        attempts,
        events,
        [(row.id, row.local_prepared_at) for row in prepared],
    )


def test_run_once_drains_queue_one_job_per_call(postgres_db: str) -> None:
    clock = FakeClock(T0)
    first = _submit(postgres_db, 1, clock)
    second = _submit(postgres_db, 2, clock)
    prepare_jobs = _fetch(
        postgres_db,
        "SELECT id, state FROM leadops.jobs WHERE kind = 'prepare_enquiry'",
    )
    assert len(prepare_jobs) == 2

    session = _factory(postgres_db)()
    try:
        crm = FakeCrm()
        handled = 0
        drained = False
        for _ in range(MAX_ITERATIONS):
            before = _succeeded_count(postgres_db)
            if not run_once(session, WORKER, clock, crm):
                drained = True
                break
            handled += 1
            # Each True call advanced exactly one job.
            assert _succeeded_count(postgres_db) == before + 1
        assert drained, (
            f"queue did not drain within {MAX_ITERATIONS} run_once calls"
        )
        assert handled >= 2

        # Both prepare jobs succeeded and both enquiries were prepared.
        states = {row.id: row.state for row in _fetch(
            postgres_db,
            "SELECT id, state FROM leadops.jobs "
            "WHERE kind = 'prepare_enquiry'",
        )}
        assert sorted(states.values()) == ["succeeded", "succeeded"]
        enquiries = _fetch(
            postgres_db,
            "SELECT id, local_prepared_at FROM leadops.enquiries",
        )
        assert {row.id for row in enquiries} == {
            first.enquiry_id,
            second.enquiry_id,
        }
        assert all(row.local_prepared_at is not None for row in enquiries)

        # A final run_once on the empty queue is a no-op.
        before_snapshot = _snapshot(postgres_db)
        assert run_once(session, WORKER, clock, crm) is False
        assert _snapshot(postgres_db) == before_snapshot
    finally:
        session.close()
