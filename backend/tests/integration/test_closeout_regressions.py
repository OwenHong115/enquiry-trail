"""Closeout regressions: readback ladder instants and operation identity.

- After an unknown create, readback NotFound reschedules must land at
  exactly T+1, T+5 and T+21 (the 1/4/16 ladder anchored at the unknown
  instant T), asserted as absolute instants.
- Create's CallContext.external_operation_id must equal the id of the
  external_operations row inserted for that create, and the readback
  context must carry the same id.
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


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


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


def _setup_sync_job(postgres_db: str, clock: FakeClock) -> uuid.UUID:
    session = _factory(postgres_db)()
    try:
        submit_intake(session, PAYLOAD, source="web", event_key=uuid.uuid4(),
                      clock=clock, routing_config=ROUTING_CONFIG)
    finally:
        session.close()
    prepare = _claim(postgres_db, clock)
    assert prepare is not None
    _handle(postgres_db, prepare, clock, FakeCrm())
    rows = _fetch(postgres_db, "SELECT id FROM leadops.jobs WHERE kind='sync_contact'")
    assert len(rows) == 1
    return rows[0].id


def _next_attempt(postgres_db: str, job_id) -> datetime:
    return _fetch(postgres_db,
                  "SELECT next_attempt_at FROM leadops.jobs WHERE id=:j",
                  {"j": job_id})[0].next_attempt_at


def test_readback_notfound_ladder_absolute_instants(postgres_db: str) -> None:
    clock = FakeClock(T0)
    job_id = _setup_sync_job(postgres_db, clock)
    # Initial lookup + two readbacks all NotFound; create commits then unknown.
    fake = FakeCrm(
        lookup_script=[NotFound(), NotFound(), NotFound()],
        create_remote_id="hs-ladder-1",
        create_commit_then_error=ProviderError("result_unknown"),
    )
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)
    assert _next_attempt(postgres_db, job_id) == T0 + timedelta(seconds=1)

    clock.advance_to(T0 + timedelta(seconds=1))
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)
    assert _next_attempt(postgres_db, job_id) == T0 + timedelta(seconds=5)

    clock.advance_to(T0 + timedelta(seconds=5))
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)
    assert _next_attempt(postgres_db, job_id) == T0 + timedelta(seconds=21)
    assert len(fake.calls_of("create_contact")) == 1


def test_create_context_carries_inserted_operation_id(postgres_db: str) -> None:
    clock = FakeClock(T0)
    job_id = _setup_sync_job(postgres_db, clock)
    fake = FakeCrm(
        lookup_script=[NotFound()],
        create_remote_id="hs-opid-1",
        create_commit_then_error=ProviderError("result_unknown"),
    )
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)

    ops = _fetch(postgres_db, "SELECT id FROM leadops.external_operations")
    assert len(ops) == 1
    operation_id = ops[0].id

    creates = fake.calls_of("create_contact")
    assert len(creates) == 1
    assert creates[0].context.external_operation_id == operation_id

    # Readback (due T+1) finds the committed contact; its context must
    # carry the same operation id. First get_contact_by_email was the
    # pre-create lookup (no operation yet), second is the readback.
    clock.advance_to(T0 + timedelta(seconds=1))
    claim = _claim(postgres_db, clock)
    assert claim is not None
    _handle(postgres_db, claim, clock, fake)
    lookups = fake.calls_of("get_contact_by_email")
    assert len(lookups) == 2
    assert lookups[0].context.external_operation_id is None
    assert lookups[1].context.external_operation_id == operation_id
