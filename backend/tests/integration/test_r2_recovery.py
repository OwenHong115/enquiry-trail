"""R2 red tests: durable intent and readback-first recovery.

The CRM sync is not crash-recoverable today: the operation intent
is inserted inside the claim transaction, immediately before the
remote create, so a process death after the provider commits rolls
the intent back and the next claim can create a second contact.
Recovery is also decided by the claim-time state snapshot instead
of by whether an operation is actually unresolved.

These tests pin the intended behaviour:

- the intent survives a crash (committed before the remote call);
- any claim with an unresolved operation does readback first and
  never re-dispatches the create while the readback is NotFound;
- a crash during reconciliation loses nothing;
- a dead credential during reconciliation blocks the provider and
  leaves the operation unresolved.

No sleeps: lease expiry is arranged by SQL on the database clock
and due times by the fake business clock.
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


def _execute(postgres_db: str, sql: str, params: dict | None = None) -> None:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
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


def _expire_lease(postgres_db: str, job_id: uuid.UUID) -> None:
    # select_claimable treats a running job as claimable only when
    # lease_until < now() on the DATABASE clock, so the fake
    # business clock cannot reclaim it; expire the lease by SQL.
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET lease_until = now() - interval '1 second' "
        "WHERE id = :job_id",
        {"job_id": job_id},
    )


def _arrange_sync_job(postgres_db: str, clock: FakeClock):
    """Submit one enquiry and drain its prepare job.

    Returns (job_id, contact row) for the single sync_contact job.
    """
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
    contact = _fetch(
        postgres_db,
        "SELECT c.id, c.version FROM leadops.contacts c "
        "JOIN leadops.enquiries e ON e.contact_id = c.id "
        "WHERE e.id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]
    return sync_job[0].id, contact


def _job(postgres_db: str, job_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT state, next_attempt_at, last_error_code FROM leadops.jobs "
        "WHERE id = :job_id",
        {"job_id": job_id},
    )[0]


def _operation(postgres_db: str, job_id: uuid.UUID):
    rows = _fetch(
        postgres_db,
        "SELECT provider, verb, status, remote_id "
        "FROM leadops.external_operations WHERE job_id = :job_id",
        {"job_id": job_id},
    )
    assert len(rows) == 1
    return rows[0]


def _links(postgres_db: str, contact_id: uuid.UUID) -> list:
    return _fetch(
        postgres_db,
        "SELECT remote_id, sync_status FROM leadops.crm_links "
        "WHERE contact_id = :contact_id",
        {"contact_id": contact_id},
    )


def _events(postgres_db: str, event_type: str) -> list:
    return _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = :event_type",
        {"event_type": event_type},
    )


def _drive_to_unknown(postgres_db: str, clock: FakeClock, fake: FakeCrm):
    """Drive one sync job to reconciling/unknown (the A12 path)."""
    job_id, contact = _arrange_sync_job(postgres_db, clock)
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)
    job = _job(postgres_db, job_id)
    assert job.state == "reconciling"
    assert job.next_attempt_at == T0 + timedelta(seconds=1)
    assert job.last_error_code == "result_unknown"
    operation = _operation(postgres_db, job_id)
    assert operation.status == "unknown"
    assert operation.remote_id is None
    return job_id, contact


def test_remote_write_lands_process_dies_search_lags(postgres_db: str) -> None:
    clock = FakeClock(T0)
    job_id, contact = _arrange_sync_job(postgres_db, clock)

    # The provider's search lags: both scripted lookups miss, yet
    # the create commits remotely before the process dies.
    fake = FakeCrm(
        lookup_script=[NotFound(), NotFound()],
        create_remote_id="hs-crash-1",
        create_commit_then_error=BaseException("simulated crash"),
    )

    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    with pytest.raises(BaseException):
        _handle(postgres_db, claim, clock, fake)

    # The remote write landed; nothing local committed.
    assert fake.remote_contact_count == 1
    assert _job(postgres_db, job_id).state == "running"
    assert _links(postgres_db, contact.id) == []
    assert _events(postgres_db, "crm.synced") == []

    # The intent must have survived the rollback: it is the only
    # record that a create may have landed remotely.
    operations = _fetch(
        postgres_db,
        "SELECT provider, verb, status, remote_id "
        "FROM leadops.external_operations WHERE job_id = :job_id",
        {"job_id": job_id},
    )
    assert len(operations) == 1
    assert operations[0].provider == "hubspot"
    assert operations[0].verb == "create"
    assert operations[0].status == "intent"
    assert operations[0].remote_id is None

    # Reclaim after lease expiry. Recovery must be readback-first:
    # the second scripted NotFound means the write is not visible
    # yet, so no second create may be dispatched.
    _expire_lease(postgres_db, job_id)
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)

    assert len(fake.calls_of("create_contact")) == 1
    assert fake.remote_contact_count == 1
    assert _job(postgres_db, job_id).state != "succeeded"
    assert _links(postgres_db, contact.id) == []
    assert _operation(postgres_db, job_id).status in ("intent", "unknown")

    # Once the provider's search catches up, the readback finds
    # the contact and recovery completes without another create.
    clock.advance_by(60)
    _expire_lease(postgres_db, job_id)
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)

    assert len(fake.calls_of("create_contact")) == 1
    assert fake.remote_contact_count == 1
    assert _job(postgres_db, job_id).state == "succeeded"
    links = _links(postgres_db, contact.id)
    assert len(links) == 1
    assert links[0].remote_id == "hs-crash-1"
    assert links[0].sync_status == "synced"
    operation = _operation(postgres_db, job_id)
    assert operation.status == "reconciled"
    assert operation.remote_id == "hs-crash-1"
    assert len(_events(postgres_db, "crm.recovered_existing")) == 1
    assert len(_events(postgres_db, "crm.synced")) == 1


def test_process_dies_during_reconciliation(postgres_db: str) -> None:
    clock = FakeClock(T0)
    fake = FakeCrm(
        lookup_script=[NotFound()],
        create_remote_id="hs-crash-2",
        create_commit_then_error=ProviderError("result_unknown"),
    )
    job_id, contact = _drive_to_unknown(postgres_db, clock, fake)
    assert len(fake.calls_of("create_contact")) == 1

    # The first readback is due at T0+1; the process dies during it.
    clock.advance_to(T0 + timedelta(seconds=1))
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    fake.lookup_script.append(BaseException("simulated crash during readback"))
    with pytest.raises(BaseException):
        _handle(postgres_db, claim, clock, fake)

    # Nothing was lost or duplicated. The claimed job is back to
    # awaiting lease-expiry reclaim (claim_next committed it as
    # running); the operation is still unresolved.
    assert _job(postgres_db, job_id).state == "running"
    assert _operation(postgres_db, job_id).status == "unknown"
    assert len(fake.calls_of("create_contact")) == 1
    assert _links(postgres_db, contact.id) == []
    assert _events(postgres_db, "crm.recovered_existing") == []
    assert _events(postgres_db, "crm.synced") == []

    # Reclaim and let the readback find the contact: recovery is
    # idempotent and still dispatches no second create.
    _expire_lease(postgres_db, job_id)
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    _handle(postgres_db, claim, clock, fake)

    assert _job(postgres_db, job_id).state == "succeeded"
    operation = _operation(postgres_db, job_id)
    assert operation.status == "reconciled"
    assert operation.remote_id == "hs-crash-2"
    links = _links(postgres_db, contact.id)
    assert len(links) == 1
    assert links[0].remote_id == "hs-crash-2"
    assert len(_events(postgres_db, "crm.recovered_existing")) == 1
    assert len(_events(postgres_db, "crm.synced")) == 1
    assert len(fake.calls_of("create_contact")) == 1
    assert fake.remote_contact_count == 1


def test_reconciliation_hits_dead_credential(postgres_db: str) -> None:
    clock = FakeClock(T0)
    fake = FakeCrm(
        lookup_script=[NotFound()],
        create_remote_id="hs-crash-3",
        create_commit_then_error=ProviderError("result_unknown"),
    )
    job_id, contact = _drive_to_unknown(postgres_db, clock, fake)
    assert len(fake.calls_of("create_contact")) == 1

    # The readback at T0+1 hits a dead credential. Today this
    # propagates and rolls back; the intended behaviour blocks
    # the provider and the job without resolving the operation.
    clock.advance_to(T0 + timedelta(seconds=1))
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_id
    fake.lookup_script.append(ProviderError("auth_failed", http_status=401))
    try:
        _handle(postgres_db, claim, clock, fake)
    except ProviderError:
        pass

    health_rows = _fetch(
        postgres_db,
        "SELECT status FROM leadops.integration_health "
        "WHERE provider = 'hubspot'",
    )
    assert len(health_rows) == 1
    assert health_rows[0].status == "blocked"

    job = _job(postgres_db, job_id)
    assert job.state == "blocked"
    assert job.last_error_code == "auth_failed"

    # A 401 readback resolves nothing.
    assert _operation(postgres_db, job_id).status == "unknown"
    assert len(fake.calls_of("create_contact")) == 1
    assert _links(postgres_db, contact.id) == []
    assert len(_events(postgres_db, "integration.blocked")) == 1
