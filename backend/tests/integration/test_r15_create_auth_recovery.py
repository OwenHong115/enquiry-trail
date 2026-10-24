"""R15 red test (test 1) and passing guard (test 2): create-401 recovery.

Defect: a create rejected with auth_failed happens BEFORE any
write (http_401_before_write), yet the handler leaves the
operation at "intent". After the operator fixes the credentials
and resume_provider requeues the job, the next claim finds an
unresolved operation, takes the readback-only path, never
dispatches a create, receives NotFound forever, and the job
eventually dies. The contact is never created.

Test 1 drives that exact flow and puts the flow assertions
first, so the red shows the consequence (no create dispatched
on the second claim) rather than the semantics. Only afterwards
does it assert the phase-1 semantics: the rejected create's
operation must have been closed as "failed" - a definite
pre-write rejection - not left at "intent" and not "unknown".

Test 2 is EXPECTED TO PASS TODAY, and is declared as such: it
exists to stop R15 from "fixing" the other, correct asymmetry.
An auth failure DURING A READBACK leaves the operation
"unknown", because the remote write may have landed; after a
credential fix the job resumes by reading back, finds the
contact the first create committed, and reconciles without a
second create.

No sleeps: every instant comes from the fake clock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import FoundContact, NotFound, ProviderError
from app.services import health
from app.services.intake import submit_intake
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER = uuid.UUID("15151515-1515-1515-1515-151515151515")
EMAIL = "create-auth@example.com"
PAYLOAD: dict = {
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": EMAIL,
    "phone": "+61412345678",
    "postcode": "2150",
    "service_type": "bathroom",
    "message": "I need a bathroom renovation next month, around AUD 20,000.",
    "preferred_contact": "email",
    "acknowledge_demo": True,
}


class _CountingCrm(FakeCrm):
    """Counts create attempts even when the create is rejected.

    FakeCrm.create_error raises before the call is recorded, so
    an attempt rejected before any write is invisible in
    calls_of("create_contact"); this counter makes it visible
    without changing what the fake records remotely.
    """

    create_attempts: int = 0

    def create_contact(self, fields, context):  # type: ignore[override]
        self.create_attempts += 1
        return super().create_contact(fields, context)


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


def _submit_and_prepare(postgres_db: str, clock: FakeClock) -> uuid.UUID:
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
    assert prepare.kind == "prepare_enquiry"
    _handle(postgres_db, prepare, clock, FakeCrm())
    return enquiry_id


def _sync_job(postgres_db: str, enquiry_id: uuid.UUID):
    rows = _fetch(
        postgres_db,
        "SELECT id, state, cycle_no, cycle_attempt_count "
        "FROM leadops.jobs WHERE kind = 'sync_contact' "
        "AND entity_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )
    assert len(rows) == 1
    return rows[0]


def _operation_status(postgres_db: str, job_id: uuid.UUID) -> str:
    rows = _fetch(
        postgres_db,
        "SELECT status FROM leadops.external_operations "
        "WHERE job_id = :job_id",
        {"job_id": job_id},
    )
    assert len(rows) == 1
    return rows[0].status


def _health_status(postgres_db: str) -> str:
    return _fetch(
        postgres_db,
        "SELECT status FROM leadops.integration_health "
        "WHERE provider = 'hubspot'",
    )[0].status


def _resume(postgres_db: str, clock: FakeClock, operator_id: object) -> bool:
    session = _factory(postgres_db)()
    try:
        return health.resume_provider(
            session,
            "hubspot",
            FakeCrm(),
            clock,
            operator_id=operator_id,  # type: ignore[arg-type]
            reason="credentials rotated",
        )
    finally:
        session.close()


def test_create_401_then_credential_fix_creates_the_contact(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(T0)
    enquiry_id = _submit_and_prepare(postgres_db, clock)
    job_id = _sync_job(postgres_db, enquiry_id).id

    # Phase 1: the create is rejected with a 401 before any write.
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_id
    rejected = _CountingCrm(
        lookup_script=[NotFound()],
        create_error=ProviderError("auth_failed", http_status=401),
    )
    _handle(postgres_db, claim, clock, rejected)

    job = _sync_job(postgres_db, enquiry_id)
    assert job.state == "blocked"
    assert _health_status(postgres_db) == "blocked"
    assert rejected.create_attempts == 1
    assert rejected.remote_contact_count == 0
    # Read now, asserted in phase 4 below: the flow assertions
    # come first so the red shows the consequence.
    operation_after_reject = _operation_status(postgres_db, job_id)
    cycle_before = (job.cycle_no, job.cycle_attempt_count)

    # Phase 2: the operator fixes the credentials and resumes.
    resumed = _resume(postgres_db, clock, seeded_operator)
    assert resumed is True
    assert _health_status(postgres_db) == "ok"
    job = _sync_job(postgres_db, enquiry_id)
    assert job.state == "pending"
    assert job.cycle_no == cycle_before[0] + 1
    assert job.cycle_attempt_count == 0

    # Phase 3: claim again and handle with a healthy provider.
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_id
    healthy = FakeCrm(lookup_script=[NotFound()])
    _handle(postgres_db, claim, clock, healthy)

    # FLOW ASSERTIONS - these are the red. Today the second claim
    # performs a readback against the dead intent, so no create
    # is dispatched and the flow stalls.
    assert len(healthy.calls_of("create_contact")) == 1, (
        "no create_contact was dispatched on the second claim: "
        "the job took the readback-only path against the "
        "rejected create's unresolved intent"
    )
    assert _sync_job(postgres_db, enquiry_id).state == "succeeded"
    links = _fetch(postgres_db, "SELECT remote_id FROM leadops.crm_links")
    assert len(links) == 1
    assert links[0].remote_id == healthy.create_remote_id
    assert _operation_status(postgres_db, job_id) == "succeeded"
    synced = _fetch(
        postgres_db,
        "SELECT id FROM leadops.activity_events "
        "WHERE event_type = 'crm.synced'",
    )
    assert len(synced) == 1

    # Phase 4 (semantics, asserted last): the rejected create's
    # operation was closed as "failed" - a definite pre-write
    # rejection - not left at "intent" and not "unknown".
    assert operation_after_reject == "failed"


def test_readback_401_keeps_unknown_and_resumes_by_readback(
    postgres_db: str, seeded_operator: object
) -> None:
    """Passing guard (passes today): do not fix this asymmetry.

    A readback 401 leaves the operation "unknown" because the
    remote write may have landed; after a credential fix the
    job resumes by reading back and reconciles, with no second
    create ever dispatched.
    """
    clock = FakeClock(T0)
    enquiry_id = _submit_and_prepare(postgres_db, clock)
    job_id = _sync_job(postgres_db, enquiry_id).id

    # The create commits remotely, then its response is lost.
    claim = _claim(postgres_db, clock)
    assert claim is not None
    first = FakeCrm(
        lookup_script=[NotFound()],
        create_commit_then_error=ProviderError("result_unknown"),
    )
    _handle(postgres_db, claim, clock, first)
    assert _sync_job(postgres_db, enquiry_id).state == "reconciling"
    assert _operation_status(postgres_db, job_id) == "unknown"

    # At +1s the readback itself is rejected with a 401.
    clock.advance_to(T0 + timedelta(seconds=1))
    claim = _claim(postgres_db, clock)
    assert claim is not None
    _handle(
        postgres_db,
        claim,
        clock,
        FakeCrm(
            by_email=ProviderError("auth_failed", http_status=401)
        ),
    )
    assert _sync_job(postgres_db, enquiry_id).state == "blocked"
    assert _health_status(postgres_db) == "blocked"
    # The asymmetry that must be preserved: still unknown.
    assert _operation_status(postgres_db, job_id) == "unknown"

    # Fix the credentials and resume.
    resumed = _resume(postgres_db, clock, seeded_operator)
    assert resumed is True
    assert _health_status(postgres_db) == "ok"

    # The next claim reads back, finds the contact the first
    # create committed, and reconciles - no second create.
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_id
    healthy = FakeCrm(
        by_email=FoundContact(
            remote_id=first.create_remote_id,
            email=EMAIL,
            firstname="Alex",
            lastname="Morgan",
            phone="+61412345678",
        )
    )
    _handle(postgres_db, claim, clock, healthy)

    assert len(healthy.calls_of("get_contact_by_email")) >= 1
    assert healthy.calls_of("create_contact") == []
    assert _sync_job(postgres_db, enquiry_id).state == "succeeded"
    assert _operation_status(postgres_db, job_id) == "reconciled"
