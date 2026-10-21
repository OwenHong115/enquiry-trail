"""S6b red test: A12 lost create response reconciles by readback.

Source contracts (transcribed by hand; plan package v3.0):
- A write whose result is unknown is never retried blindly: the
  job moves to reconciling, readbacks happen at the contract
  delays (1/4/16 seconds), and recovery proceeds unknown ->
  recovered_existing -> synced, in that causal order.
- The first readback is due 1 second after the unknown result.

No sleeps: every instant is arranged with the fake clock.
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


def test_a12_lost_create_response_reconciles_by_readback(
    postgres_db: str,
) -> None:
    clock = FakeClock(T0)
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

    prepare = _claim(postgres_db, clock)
    assert prepare is not None
    _handle(postgres_db, prepare, clock, FakeCrm())

    sync_job = _fetch(
        postgres_db, "SELECT id FROM leadops.jobs WHERE kind = 'sync_contact'"
    )
    assert len(sync_job) == 1
    job_id = sync_job[0].id
    contact = _fetch(
        postgres_db,
        "SELECT c.id, c.version FROM leadops.contacts c "
        "JOIN leadops.enquiries e ON e.contact_id = c.id "
        "WHERE e.id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]

    # The create commits remotely as hs-lost-1, then the response is
    # lost (result_unknown). Later lookups discover that contact.
    fake = FakeCrm(
        lookup_script=[NotFound()],
        create_remote_id="hs-lost-1",
        create_commit_then_error=ProviderError("result_unknown"),
    )

    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)

    job = _fetch(
        postgres_db,
        "SELECT state, next_attempt_at, last_error_code FROM leadops.jobs "
        "WHERE id = :job_id",
        {"job_id": job_id},
    )[0]
    # Contract A12: unknown result -> reconciling, first readback
    # due after the contract's first reconciliation delay (1s).
    assert job.state == "reconciling"
    assert job.next_attempt_at == T0 + timedelta(seconds=1)
    assert job.last_error_code == "result_unknown"

    attempts = _fetch(
        postgres_db,
        "SELECT error_code FROM leadops.job_attempts WHERE job_id = :job_id",
        {"job_id": job_id},
    )
    assert len(attempts) == 1
    assert attempts[0].error_code == "result_unknown"

    operations = _fetch(
        postgres_db,
        "SELECT id, provider, verb, status, remote_id, attempt_count "
        "FROM leadops.external_operations",
    )
    assert len(operations) == 1
    operation = operations[0]
    assert operation.provider == "hubspot"
    assert operation.verb == "create"
    assert operation.status == "unknown"
    assert operation.remote_id is None
    assert operation.attempt_count >= 1

    unknown_events = _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = 'crm.operation_unknown'",
    )
    assert len(unknown_events) == 1
    assert unknown_events[0].details_json["operation_id"] == str(operation.id)
    assert len(fake.calls_of("create_contact")) == 1

    # The readback is due at T0+1, not before.
    clock.advance_to(T0 + timedelta(seconds=0.999))
    assert _claim(postgres_db, clock) is None
    clock.advance_to(T0 + timedelta(seconds=1))
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id

    _handle(postgres_db, claim, clock, fake)

    job = _fetch(
        postgres_db,
        "SELECT state FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0]
    assert job.state == "succeeded"

    link = _fetch(
        postgres_db,
        "SELECT remote_id, sync_status, synced_contact_version "
        "FROM leadops.crm_links WHERE contact_id = :contact_id",
        {"contact_id": contact.id},
    )[0]
    assert link.remote_id == "hs-lost-1"
    assert link.sync_status == "synced"
    assert link.synced_contact_version == contact.version

    operation = _fetch(
        postgres_db,
        "SELECT status, remote_id, last_checked_at "
        "FROM leadops.external_operations WHERE id = :operation_id",
        {"operation_id": operation.id},
    )[0]
    assert operation.status == "reconciled"
    assert operation.remote_id == "hs-lost-1"
    assert operation.last_checked_at is not None

    recovered = _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = 'crm.recovered_existing'",
    )
    assert len(recovered) == 1
    assert recovered[0].details_json["operation_id"] == str(operations[0].id)
    assert recovered[0].details_json["remote_id"] == "hs-lost-1"
    synced = _fetch(
        postgres_db,
        "SELECT id FROM leadops.activity_events "
        "WHERE event_type = 'crm.synced'",
    )
    assert len(synced) == 1

    # Causal order on the timeline: unknown, then recovered, then
    # synced — never any other order and never a second create.
    sequence = _fetch(
        postgres_db,
        "SELECT event_type FROM leadops.activity_events "
        "WHERE event_type IN ('crm.operation_unknown', "
        "'crm.recovered_existing', 'crm.synced') ORDER BY event_seq",
    )
    assert [row.event_type for row in sequence] == [
        "crm.operation_unknown",
        "crm.recovered_existing",
        "crm.synced",
    ]
    assert len(fake.calls_of("create_contact")) == 1
    assert fake.remote_contact_count == 1
