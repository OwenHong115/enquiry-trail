"""S6c red tests: A16 auth blocking and operator resume.

Source contracts (transcribed by hand; plan package v3.0):
- running -> blocked on provider_auth_failed (block the provider,
  open one incident); blocked -> pending on a passed credential
  probe (new cycle, audited operator action).
- integration_health is keyed by provider; integration.blocked
  carries provider + incident_id, integration.resumed carries
  provider + incident_id + reason.

No sleeps: every instant comes from the fake clock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import ProviderError
from app.services import health
from app.services.intake import submit_intake
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER = uuid.UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")


def _payload(email: str, message: str) -> dict:
    return {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": email,
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": message,
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }


PAYLOAD_ONE = _payload(
    "alex.demo@example.com",
    "I need a bathroom renovation next month. My budget is around AUD 20,000.",
)
PAYLOAD_TWO = _payload(
    "sam.lee@example.com",
    "Kitchen renovation quote please, ideally starting this quarter.",
)


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


def _arrange_two_sync_jobs(postgres_db: str, clock: FakeClock) -> list[uuid.UUID]:
    enquiry_ids: list[uuid.UUID] = []
    for payload in (PAYLOAD_ONE, PAYLOAD_TWO):
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
            enquiry_ids.append(result.enquiry_id)
        finally:
            session.close()
    for _ in enquiry_ids:
        prepare = _claim(postgres_db, clock)
        assert prepare is not None
        session = _factory(postgres_db)()
        try:
            handle_claim(session, prepare, clock)
        finally:
            session.close()
    jobs = _fetch(
        postgres_db,
        "SELECT id, entity_id FROM leadops.jobs WHERE kind = 'sync_contact' "
        "ORDER BY created_at, id",
    )
    assert len(jobs) == 2
    return [row.id for row in jobs]


def _block_provider(postgres_db: str, clock: FakeClock) -> list[uuid.UUID]:
    """Arrange two sync jobs and block the provider on the first."""
    job_ids = _arrange_two_sync_jobs(postgres_db, clock)
    failing = FakeCrm(
        lookup_script=[ProviderError("auth_failed", http_status=401)]
    )
    claim = _claim(postgres_db, clock)
    assert claim is not None and claim.job_id == job_ids[0]
    _handle(postgres_db, claim, clock, failing)
    return job_ids


def _health_row(postgres_db: str):
    rows = _fetch(
        postgres_db,
        "SELECT status, incident_id, blocked_at, last_error_code "
        "FROM leadops.integration_health WHERE provider = 'hubspot'",
    )
    assert len(rows) == 1
    return rows[0]


def _job(postgres_db: str, job_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT state, last_error_code, cycle_no, next_attempt_at "
        "FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0]


def _events(postgres_db: str, event_type: str) -> list:
    return _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = :event_type",
        {"event_type": event_type},
    )


def test_a16_auth_failure_blocks_provider(postgres_db: str) -> None:
    clock = FakeClock(T0)
    job_ids = _block_provider(postgres_db, clock)
    job_id = job_ids[0]

    job = _job(postgres_db, job_id)
    # Contract: provider_auth_failed moves the job to blocked.
    assert job.state == "blocked"
    assert job.last_error_code == "auth_failed"

    row = _health_row(postgres_db)
    assert row.status == "blocked"
    assert row.incident_id is not None
    assert row.blocked_at is not None
    assert row.last_error_code == "auth_failed"

    blocked_events = _events(postgres_db, "integration.blocked")
    assert len(blocked_events) == 1
    assert blocked_events[0].details_json["provider"] == "hubspot"
    assert blocked_events[0].details_json["incident_id"] == str(row.incident_id)

    attempts = _fetch(
        postgres_db,
        "SELECT error_code FROM leadops.job_attempts WHERE job_id = :job_id",
        {"job_id": job_id},
    )
    assert len(attempts) == 1
    assert attempts[0].error_code == "auth_failed"

    links = _fetch(postgres_db, "SELECT id FROM leadops.crm_links")
    assert links == []


def test_a16_no_writes_while_blocked(postgres_db: str) -> None:
    clock = FakeClock(T0)
    job_ids = _block_provider(postgres_db, clock)
    incident_before = _health_row(postgres_db).incident_id

    would_succeed = FakeCrm()
    claim = _claim(postgres_db, clock)
    # The second job may be claimed, but handling it must not reach
    # the provider at all while the provider is blocked.
    assert claim is not None and claim.job_id == job_ids[1]
    _handle(postgres_db, claim, clock, would_succeed)

    assert would_succeed.calls == []
    assert _job(postgres_db, job_ids[1]).state == "blocked"
    assert len(_events(postgres_db, "integration.blocked")) == 1
    assert _health_row(postgres_db).incident_id == incident_before


def test_a16_failed_probe_keeps_blocking(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(T0)
    job_ids = _block_provider(postgres_db, clock)
    incident_before = _health_row(postgres_db).incident_id

    failing_probe = FakeCrm(probe_ok=False)
    session = _factory(postgres_db)()
    try:
        resumed = health.resume_provider(
            session,
            "hubspot",
            failing_probe,
            clock,
            operator_id=seeded_operator,  # type: ignore[arg-type]
            reason="Rotated the token",
        )
    finally:
        session.close()

    # Seam contract: a failed probe returns False, never raises.
    assert resumed is False
    assert _health_row(postgres_db).status == "blocked"
    assert _health_row(postgres_db).incident_id == incident_before
    assert _job(postgres_db, job_ids[0]).state == "blocked"
    assert _job(postgres_db, job_ids[1]).state == "blocked"
    assert _events(postgres_db, "integration.resumed") == []
    assert failing_probe.calls_of("create_contact") == []
    assert failing_probe.calls_of("patch_contact") == []


def test_a16_successful_probe_resumes_and_requeues(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(T0)
    job_ids = _block_provider(postgres_db, clock)
    incident_id = _health_row(postgres_db).incident_id
    cycles_before = {job_id: _job(postgres_db, job_id).cycle_no for job_id in job_ids}

    clock.advance_by(3600)
    resumed_at = clock.now_utc()
    healthy = FakeCrm(probe_ok=True)
    session = _factory(postgres_db)()
    try:
        resumed = health.resume_provider(
            session,
            "hubspot",
            healthy,
            clock,
            operator_id=seeded_operator,  # type: ignore[arg-type]
            reason="Rotated the token",
        )
    finally:
        session.close()

    assert resumed is True
    assert _health_row(postgres_db).status == "ok"
    for job_id in job_ids:
        job = _job(postgres_db, job_id)
        # Contract: blocked -> pending is a new cycle, due now.
        assert job.state == "pending"
        assert job.next_attempt_at == resumed_at
        assert job.cycle_no == cycles_before[job_id] + 1

    resumed_events = _events(postgres_db, "integration.resumed")
    assert len(resumed_events) == 1
    assert resumed_events[0].details_json["provider"] == "hubspot"
    assert resumed_events[0].details_json["incident_id"] == str(incident_id)
    assert resumed_events[0].details_json["reason"] == "Rotated the token"

    # Requeued work then syncs normally.
    fake = FakeCrm()
    claim = _claim(postgres_db, clock)
    assert claim is not None
    _handle(postgres_db, claim, clock, fake)
    assert len(fake.calls_of("create_contact")) == 1
    links = _fetch(postgres_db, "SELECT id FROM leadops.crm_links")
    assert len(links) == 1
    assert len(_events(postgres_db, "crm.synced")) == 1
