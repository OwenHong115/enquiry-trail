"""R8 red tests: lease fencing after the transaction split.

R2 split one transaction into sequentially committed blocks.
Before the split, the claim transaction held the job row lock
until commit, so no other worker could reclaim the job while it
was being handled. Now block 1 (the fence in
app/worker/claiming.py) commits and releases the lock, the
provider call runs outside any transaction, and the finalization
blocks write without re-checking the lease:

- _handle_readback writes in its own committed blocks;
- _record_provider_failure writes in its own blocks;
- mark_job_succeeded, move_job_to_blocked, move_job_to_reconciling,
  reschedule_reconciling_job, schedule_job_retry and
  move_job_to_dead in app/db/job_repo.py all update
  WHERE id = job_id only, with no lease_token condition.

So a worker whose lease was stolen can overwrite the new owner's
result. Both tests interleave the steal synchronously from the
FakeCrm before_create hook: deterministic, no threads, no sleeps.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.ports.crm import ProviderError
from app.services.intake import submit_intake
from app.worker.claiming import ClaimedJob, claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WORKER_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
PAYLOAD: dict = {
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": "fencing@example.com",
    "phone": "+61412345678",
    "postcode": "2150",
    "service_type": "bathroom",
    "message": "I need a bathroom renovation next month, around AUD 20,000.",
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


def _job_row(postgres_db: str, job_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT state, lease_token FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0]


def _links(postgres_db: str, contact_id: uuid.UUID) -> list:
    return _fetch(
        postgres_db,
        "SELECT remote_id FROM leadops.crm_links "
        "WHERE contact_id = :contact_id",
        {"contact_id": contact_id},
    )


def _events(postgres_db: str, event_type: str) -> list:
    return _fetch(
        postgres_db,
        "SELECT id FROM leadops.activity_events "
        "WHERE event_type = :event_type",
        {"event_type": event_type},
    )


def _attempt_finished_at(
    postgres_db: str, job_id: uuid.UUID, attempt_no: int
):
    return _fetch(
        postgres_db,
        "SELECT finished_at FROM leadops.job_attempts "
        "WHERE job_id = :job_id AND attempt_no = :attempt_no",
        {"job_id": job_id, "attempt_no": attempt_no},
    )[0].finished_at


def _setup_sync_job(postgres_db: str, clock: FakeClock):
    """Submit one enquiry and drain its prepare job.

    Returns (contact_id, sync_job_id) with the sync job still
    pending.
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

    session = _factory(postgres_db)()
    try:
        prepare = claim_next(session, WORKER_A, clock)
        assert prepare is not None
        handle_claim(session, prepare, clock, crm=FakeCrm())
    finally:
        session.close()

    sync_job_id = _fetch(
        postgres_db,
        "SELECT id FROM leadops.jobs WHERE kind = 'sync_contact'",
    )[0].id
    contact_id = _fetch(
        postgres_db,
        "SELECT contact_id FROM leadops.enquiries WHERE id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0].contact_id
    return contact_id, sync_job_id


def _steal(
    postgres_db: str, clock: FakeClock, job_id: uuid.UUID, worker_b: uuid.UUID
) -> ClaimedJob:
    """Expire the lease by SQL, then reclaim on a fresh session."""
    _execute(
        postgres_db,
        "UPDATE leadops.jobs "
        "SET lease_until = now() - interval '1 second' "
        "WHERE id = :job_id",
        {"job_id": job_id},
    )
    session = _factory(postgres_db)()
    try:
        claim = claim_next(session, worker_b, clock)
    finally:
        session.close()
    assert claim is not None
    assert claim.job_id == job_id
    return claim


def _claim_a(
    postgres_db: str, clock: FakeClock, sync_job_id: uuid.UUID
) -> tuple[Session, ClaimedJob]:
    session = _factory(postgres_db)()
    claim = claim_next(session, WORKER_A, clock)
    assert claim is not None
    assert claim.job_id == sync_job_id
    assert claim.kind == "sync_contact"
    return session, claim


def test_stale_worker_must_not_finalize_a_success(postgres_db: str) -> None:
    clock = FakeClock(T0)
    contact_id, sync_job_id = _setup_sync_job(postgres_db, clock)
    session_a, claim_a = _claim_a(postgres_db, clock, sync_job_id)

    stolen: dict[str, ClaimedJob] = {}

    def steal_once() -> None:
        if "claim" not in stolen:
            stolen["claim"] = _steal(postgres_db, clock, sync_job_id, WORKER_B)

    fake = FakeCrm(before_create=steal_once)
    try:
        # Must return normally, not raise, even though the lease
        # was stolen while A was inside create_contact.
        handle_claim(
            session_a, claim_a, clock, crm=fake, scheduler=FakeScheduler()
        )
    finally:
        session_a.close()
    claim_b = stolen["claim"]

    # A wrote nothing: B still owns the job.
    job = _job_row(postgres_db, sync_job_id)
    assert job.state == "running"
    assert job.lease_token == claim_b.lease_token
    assert _links(postgres_db, contact_id) == []
    assert _events(postgres_db, "crm.synced") == []
    assert _attempt_finished_at(postgres_db, sync_job_id, claim_a.attempt_no) is None

    # B then handles its claim and the run recovers cleanly: the
    # remote contact A created is found by readback, so no second
    # create is ever dispatched.
    session_b = _factory(postgres_db)()
    try:
        handle_claim(
            session_b, claim_b, clock, crm=fake, scheduler=FakeScheduler()
        )
    finally:
        session_b.close()

    assert _job_row(postgres_db, sync_job_id).state == "succeeded"
    links = _links(postgres_db, contact_id)
    assert len(links) == 1
    assert links[0].remote_id == fake.create_remote_id
    assert len(_events(postgres_db, "crm.synced")) == 1
    operations = _fetch(
        postgres_db,
        "SELECT status FROM leadops.external_operations "
        "WHERE job_id = :job_id",
        {"job_id": sync_job_id},
    )
    assert len(operations) == 1
    assert operations[0].status == "reconciled"
    assert len(fake.calls_of("create_contact")) == 1


def test_stale_worker_must_not_reschedule_or_block_new_owner(
    postgres_db: str,
) -> None:
    clock = FakeClock(T0)
    contact_id, sync_job_id = _setup_sync_job(postgres_db, clock)
    session_a, claim_a = _claim_a(postgres_db, clock, sync_job_id)

    stolen: dict[str, ClaimedJob] = {}

    def steal_once() -> None:
        if "claim" not in stolen:
            stolen["claim"] = _steal(postgres_db, clock, sync_job_id, WORKER_B)

    fake = FakeCrm(
        before_create=steal_once,
        create_error=ProviderError("unavailable"),
    )
    try:
        # The steal happens, then A's create fails retryably. A
        # must return normally and must not steal the job back.
        handle_claim(
            session_a, claim_a, clock, crm=fake, scheduler=FakeScheduler()
        )
    finally:
        session_a.close()
    claim_b = stolen["claim"]

    job = _job_row(postgres_db, sync_job_id)
    assert job.state == "running"
    assert job.lease_token == claim_b.lease_token
    assert _events(postgres_db, "job.retry_scheduled") == []
    assert _links(postgres_db, contact_id) == []
    assert _attempt_finished_at(postgres_db, sync_job_id, claim_a.attempt_no) is None
