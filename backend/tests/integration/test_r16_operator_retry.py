"""R16 red tests: operator retry of a dead job (contract A18).

POST /api/v1/jobs/{id}/retry does not exist yet. The published
contract defines it as:

- security OperatorSession, header X-CSRF-Token, path id uuid;
- request body RetryJob, additionalProperties false, required:
  expected_state (one of "dead" or "blocked"),
  expected_updated_at (date-time), reason (10..500 characters);
- 200 returns Job: id, kind, state, cycle_no, attempt_count,
  cycle_attempt_count, next_attempt_at, updated_at;
- 404 unknown job, 409 on a stale expected_updated_at,
  401/403 for session/CSRF, 422 validation.

The transition is behaviour.yaml's "dead -> pending, trigger
operator_retry, effects new_cycle", and it must emit exactly
one job.manual_retry event (actor operator, severity info,
details {job_id, reason, cycle_no}).

Test 2's concurrency proof is deterministic by design: the
contract's optimistic concurrency (expected_state +
expected_updated_at) replaces any need for threads. A second
call carrying the pre-retry updated_at is stale no matter when
it arrives, so no interleaving has to be arranged.

Note on visibility: activity_events has no visibility column,
so the contract's "audit-only" visibility for this event
cannot be stored yet. The tests pin actor, severity and
details - the parts the schema can honestly hold - and do not
pretend otherwise.

Today the route does not exist, so every retry call answers
404: the red is the absent endpoint and, for test 1, the
missing transition and event behind it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import FoundContact, NotFound, ProviderError
from app.services.intake import submit_intake
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
WORKER = uuid.UUID("16161616-1616-1616-1616-161616161616")
CSRF_HEADER = "X-CSRF-Token"
REASON = "credentials rotated by operator"

JOB_KEYS = {
    "id",
    "kind",
    "state",
    "cycle_no",
    "attempt_count",
    "cycle_attempt_count",
    "next_attempt_at",
    "updated_at",
}


def _payload(email: str) -> dict:
    return {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": email,
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


def _job_row(postgres_db: str, job_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT id, kind, entity_id, state, cycle_no, attempt_count, "
        "cycle_attempt_count, next_attempt_at, updated_at "
        "FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0]


def _sync_job_id(postgres_db: str, enquiry_id: uuid.UUID) -> uuid.UUID:
    return _fetch(
        postgres_db,
        "SELECT id FROM leadops.jobs WHERE kind = 'sync_contact' "
        "AND entity_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0].id


def _operation_status(postgres_db: str, job_id: uuid.UUID) -> str:
    rows = _fetch(
        postgres_db,
        "SELECT status FROM leadops.external_operations "
        "WHERE job_id = :job_id",
        {"job_id": job_id},
    )
    assert len(rows) == 1
    return rows[0].status


def _manual_retry_events(postgres_db: str, job_id: uuid.UUID) -> list:
    return _fetch(
        postgres_db,
        "SELECT actor_type, severity, details_json "
        "FROM leadops.activity_events "
        "WHERE event_type = 'job.manual_retry' AND job_id = :job_id",
        {"job_id": job_id},
    )


def _drive_dead_sync(
    postgres_db: str, clock: FakeClock, email: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Submit, drain prepare, kill the sync with a permanent error.

    Returns (enquiry_id, sync_job_id) with the job dead and its
    operation "failed".
    """
    session = _factory(postgres_db)()
    try:
        result = submit_intake(
            session,
            _payload(email),
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

    job_id = _sync_job_id(postgres_db, enquiry_id)
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_id
    _handle(
        postgres_db,
        claim,
        clock,
        FakeCrm(create_error=ProviderError("validation_failed")),
    )
    assert _job_row(postgres_db, job_id).state == "dead"
    return enquiry_id, job_id


def _retry(
    api_client: TestClient,
    job_id: uuid.UUID,
    expected_updated_at: datetime,
    *,
    expected_state: str = "dead",
    reason: str = REASON,
):
    return api_client.post(
        f"/api/v1/jobs/{job_id}/retry",
        json={
            "expected_state": expected_state,
            "expected_updated_at": expected_updated_at.isoformat(),
            "reason": reason,
        },
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )


def _parse_instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_dead_job_is_reopened_with_one_audit_event(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    _, job_id = _drive_dead_sync(postgres_db, clock, "retry-one@example.com")
    before = _job_row(postgres_db, job_id)

    response = _retry(api_client, job_id, before.updated_at)
    assert response.status_code == 200, response.text
    body = response.json()

    # Exactly the eight Job keys, with the new cycle published.
    assert set(body.keys()) == JOB_KEYS
    assert body["id"] == str(job_id)
    assert body["kind"] == "sync_contact"
    assert body["state"] == "pending"
    assert body["cycle_no"] == before.cycle_no + 1
    assert body["cycle_attempt_count"] == 0
    assert body["attempt_count"] == before.attempt_count
    assert body["next_attempt_at"] is not None
    assert _parse_instant(body["updated_at"]) > before.updated_at

    # The database row agrees with the response.
    row = _job_row(postgres_db, job_id)
    assert row.state == "pending"
    assert row.cycle_no == body["cycle_no"]
    assert row.cycle_attempt_count == 0
    assert row.attempt_count == before.attempt_count
    assert row.next_attempt_at == _parse_instant(body["next_attempt_at"])
    assert row.updated_at == _parse_instant(body["updated_at"])

    # Exactly one audit event for this reopening.
    events = _manual_retry_events(postgres_db, job_id)
    assert len(events) == 1
    assert events[0].actor_type == "operator"
    assert events[0].severity == "info"
    assert events[0].details_json["job_id"] == str(job_id)
    assert events[0].details_json["reason"] == REASON
    assert events[0].details_json["cycle_no"] == body["cycle_no"]


def test_stale_retry_is_a_clean_conflict_not_a_second_cycle(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    _, job_id = _drive_dead_sync(postgres_db, clock, "retry-two@example.com")
    before = _job_row(postgres_db, job_id)

    first = _retry(api_client, job_id, before.updated_at)
    assert first.status_code == 200, first.text
    first_cycle = first.json()["cycle_no"]

    # The same pre-retry body again: its expected_updated_at is
    # now stale, deterministically, whenever it arrives.
    second = _retry(api_client, job_id, before.updated_at)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] in (
        "version_conflict",
        "invalid_state",
    )

    row = _job_row(postgres_db, job_id)
    assert row.state == "pending"
    assert row.cycle_no == first_cycle
    assert len(_manual_retry_events(postgres_db, job_id)) == 1

    # A mismatched expected_state against the CURRENT row is a
    # conflict too: the value "blocked" is legal, so this is a
    # state precondition failure (409), not a validation error.
    current = _job_row(postgres_db, job_id)
    mismatch = _retry(
        api_client, job_id, current.updated_at, expected_state="blocked"
    )
    assert mismatch.status_code == 409, mismatch.text
    assert len(_manual_retry_events(postgres_db, job_id)) == 1

    # Unknown job: 404 in the project envelope.
    unknown = _retry(
        api_client, uuid.uuid4(), datetime.now(timezone.utc)
    )
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "not_found"


def test_recovery_follows_the_unresolved_operation(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(datetime.now(timezone.utc))

    # (a) Unknown operation: retried by READBACK, never re-created.
    session = _factory(postgres_db)()
    try:
        result = submit_intake(
            session,
            _payload("retry-unknown@example.com"),
            source=SOURCE,
            event_key=uuid.uuid4(),
            clock=clock,
            routing_config=ROUTING_CONFIG,
        )
        enquiry_a = result.enquiry_id
    finally:
        session.close()
    prepare = _claim(postgres_db, clock)
    assert prepare is not None
    _handle(postgres_db, prepare, clock, FakeCrm())
    job_a = _sync_job_id(postgres_db, enquiry_a)

    claim = _claim(postgres_db, clock)
    assert claim is not None
    first = FakeCrm(
        lookup_script=[NotFound()],
        create_commit_then_error=ProviderError("result_unknown"),
    )
    _handle(postgres_db, claim, clock, first)
    assert _operation_status(postgres_db, job_a) == "unknown"

    # Exhaust the budget through NotFound readbacks until the
    # claim-time closure marks the job dead.
    readbacks = FakeCrm()
    for _ in range(6):
        job = _job_row(postgres_db, job_a)
        if job.state == "dead":
            break
        clock.advance_to(job.next_attempt_at)
        claim = _claim(postgres_db, clock)
        if claim is None:
            assert _job_row(postgres_db, job_a).state == "dead"
            break
        _handle(postgres_db, claim, clock, readbacks)
    assert _job_row(postgres_db, job_a).state == "dead"
    assert _operation_status(postgres_db, job_a) == "unknown"

    retried = _retry(api_client, job_a, _job_row(postgres_db, job_a).updated_at)
    assert retried.status_code == 200, retried.text

    clock.advance_to(datetime.now(timezone.utc))
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_a
    healthy_readback = FakeCrm(
        by_email=FoundContact(
            remote_id=first.create_remote_id,
            email="retry-unknown@example.com",
            firstname="Alex",
            lastname="Morgan",
            phone="+61412345678",
        )
    )
    _handle(postgres_db, claim, clock, healthy_readback)

    assert healthy_readback.calls_of("create_contact") == []
    assert _job_row(postgres_db, job_a).state == "succeeded"
    assert _operation_status(postgres_db, job_a) == "reconciled"

    # (b) Failed operation: retried by CREATE.
    enquiry_b, job_b = _drive_dead_sync(
        postgres_db, clock, "retry-failed@example.com"
    )
    assert _operation_status(postgres_db, job_b) == "failed"
    contact_b = _fetch(
        postgres_db,
        "SELECT contact_id FROM leadops.enquiries WHERE id = :id",
        {"id": enquiry_b},
    )[0].contact_id

    retried = _retry(api_client, job_b, _job_row(postgres_db, job_b).updated_at)
    assert retried.status_code == 200, retried.text

    clock.advance_to(datetime.now(timezone.utc))
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_b
    healthy_create = FakeCrm(lookup_script=[NotFound()])
    _handle(postgres_db, claim, clock, healthy_create)

    assert len(healthy_create.calls_of("create_contact")) == 1
    assert _job_row(postgres_db, job_b).state == "succeeded"
    links = _fetch(
        postgres_db,
        "SELECT id FROM leadops.crm_links WHERE contact_id = :contact_id",
        {"contact_id": contact_b},
    )
    assert len(links) == 1
