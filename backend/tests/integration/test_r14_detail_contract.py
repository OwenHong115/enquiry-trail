"""R14 red tests: the detail publishes only contract statuses.

The detail endpoint currently publishes internal job states as
public statuses. The authoritative contract (transcribed by
hand; the plan package is read-only and must not be copied into
this repository) gives these enums:

  ProcessingSummary: processing, waiting_retry, blocked,
                     reconciling, ready, failed
  CrmStatus:         pending, syncing, waiting_retry,
                     reconciling, synced, blocked, failed
  Attention:         none, review_required, retry_scheduled,
                     notification_uncertain,
                     integration_blocked, intervention_required

and this per-state table (summary, attention, crm.status,
crm.next_attempt_at):

  processing     processing   none                  pending        null
  waiting_retry  waiting_retry retry_scheduled      waiting_retry  set
  blocked        blocked      integration_blocked   blocked        null
  reconciling    reconciling  none                  reconciling    null
  ready          ready        none                  synced         null
  failed (dead)  failed       intervention_required failed         null

Also per the contract: local_prepare_dead (the prepare job for
THIS enquiry is dead) forces processing_summary "failed".

related_jobs is [] in every contract fixture, so leaving it []
is correct and it is not touched here.

Current violations the tests pin: attention emits "blocked",
"reconciling" and "dead" (none are legal); processing_summary
emits "dead"; crm.status emits "dead", "running" and
"succeeded"; and crm.next_attempt_at is set for reconciling
where the contract requires null.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
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
WORKER = uuid.UUID("14141414-1414-1414-1414-141414141414")

PROCESSING_SUMMARIES = {
    "processing",
    "waiting_retry",
    "blocked",
    "reconciling",
    "ready",
    "failed",
}
CRM_STATUSES = {
    "pending",
    "syncing",
    "waiting_retry",
    "reconciling",
    "synced",
    "blocked",
    "failed",
}
ATTENTIONS = {
    "none",
    "review_required",
    "retry_scheduled",
    "notification_uncertain",
    "integration_blocked",
    "intervention_required",
}

DETAIL_KEYS = {
    "id",
    "display_no",
    "contact",
    "service_type",
    "postcode",
    "owner",
    "processing_summary",
    "attention",
    "requires_review",
    "crm",
    "received_at",
    "version",
    "message",
    "preferred_contact",
    "scenario_run_id",
    "routing_reason",
    "rules_hash",
    "review_reason_codes",
    "review_resolution_note",
    "task",
    "enrichment",
    "draft",
    "notification",
    "local_prepared_at",
    "required_contact_version",
    "related_jobs",
}


def _payload(email: str, **overrides: object) -> dict:
    payload: dict = {
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
    payload.update(overrides)
    return payload


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


def _submit(postgres_db: str, clock: FakeClock, email: str, **overrides):
    session = _factory(postgres_db)()
    try:
        return submit_intake(
            session,
            _payload(email, **overrides),
            source=SOURCE,
            event_key=uuid.uuid4(),
            clock=clock,
            routing_config=ROUTING_CONFIG,
        )
    finally:
        session.close()


def _run_next(postgres_db: str, clock: FakeClock, fake: FakeCrm):
    session = _factory(postgres_db)()
    try:
        claim = claim_next(session, WORKER, clock)
        assert claim is not None
        handle_claim(
            session, claim, clock, crm=fake, scheduler=FakeScheduler()
        )
        return claim
    finally:
        session.close()


def _detail(api_client: TestClient, enquiry_id: uuid.UUID) -> dict:
    response = api_client.get(f"/api/v1/enquiries/{enquiry_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _assert_contract_state(body: dict, summary, attention, crm_status, next_set):
    assert set(body.keys()) == DETAIL_KEYS
    # Enum membership first, with the offending value named, so
    # no internal state can leak through unnoticed.
    assert body["processing_summary"] in PROCESSING_SUMMARIES, (
        f"processing_summary {body['processing_summary']!r} "
        "is not a contract ProcessingSummary"
    )
    assert body["attention"] in ATTENTIONS, (
        f"attention {body['attention']!r} is not a contract Attention"
    )
    assert body["crm"]["status"] in CRM_STATUSES, (
        f"crm.status {body['crm']['status']!r} is not a contract CrmStatus"
    )
    assert body["processing_summary"] == summary
    assert body["attention"] == attention
    assert body["crm"]["status"] == crm_status
    if next_set:
        assert body["crm"]["next_attempt_at"] is not None
    else:
        assert body["crm"]["next_attempt_at"] is None


def test_every_state_publishes_only_contract_values(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(T0)
    # One healthy fake for every successful drive in this test:
    # its remote ids are distinct per contact, while separate
    # FakeCrm instances would each hand out "hs-created-1" and
    # collide on ux_crm_remote.
    healthy = FakeCrm()

    # processing: submitted, prepare not yet drained.
    processing = _submit(postgres_db, clock, "state-processing@example.com")
    _assert_contract_state(
        _detail(api_client, processing.enquiry_id),
        "processing",
        "none",
        "pending",
        False,
    )
    # Clear this enquiry's jobs so later claims stay targeted.
    _run_next(postgres_db, clock, healthy)
    _run_next(postgres_db, clock, healthy)

    # waiting_retry: the sync fails retryably.
    waiting = _submit(postgres_db, clock, "state-waiting@example.com")
    _run_next(postgres_db, clock, healthy)
    _run_next(
        postgres_db,
        clock,
        FakeCrm(create_error=ProviderError("unavailable")),
    )
    _assert_contract_state(
        _detail(api_client, waiting.enquiry_id),
        "waiting_retry",
        "retry_scheduled",
        "waiting_retry",
        True,
    )

    # reconciling: the create's result is unknown.
    reconciling = _submit(postgres_db, clock, "state-reconciling@example.com")
    _run_next(postgres_db, clock, healthy)
    _run_next(
        postgres_db,
        clock,
        FakeCrm(
            lookup_script=[NotFound()],
            create_commit_then_error=ProviderError("result_unknown"),
        ),
    )
    _assert_contract_state(
        _detail(api_client, reconciling.enquiry_id),
        "reconciling",
        "none",
        "reconciling",
        False,
    )

    # failed: the sync dies permanently (internal state "dead").
    failed = _submit(postgres_db, clock, "state-failed@example.com")
    _run_next(postgres_db, clock, healthy)
    _run_next(
        postgres_db,
        clock,
        FakeCrm(create_error=ProviderError("validation_failed")),
    )
    _assert_contract_state(
        _detail(api_client, failed.enquiry_id),
        "failed",
        "intervention_required",
        "failed",
        False,
    )

    # ready: prepare and sync both succeed.
    ready = _submit(postgres_db, clock, "state-ready@example.com")
    _run_next(postgres_db, clock, healthy)
    _run_next(postgres_db, clock, healthy)
    _assert_contract_state(
        _detail(api_client, ready.enquiry_id),
        "ready",
        "none",
        "synced",
        False,
    )

    # blocked: the sync hits a dead credential. Last, because
    # blocking the provider is global state in this database.
    blocked = _submit(postgres_db, clock, "state-blocked@example.com")
    _run_next(postgres_db, clock, healthy)
    _run_next(
        postgres_db,
        clock,
        FakeCrm(create_error=ProviderError("auth_failed")),
    )
    _assert_contract_state(
        _detail(api_client, blocked.enquiry_id),
        "blocked",
        "integration_blocked",
        "blocked",
        False,
    )


def test_dead_prepare_job_is_visible(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(
        datetime.now(timezone.utc).replace(microsecond=0)
    )
    # No postcode: requires_review is true, which is fine; the
    # prepare-dead rule must still win the summary.
    session = _factory(postgres_db)()
    try:
        payload = _payload("prepare-dead@example.com")
        del payload["postcode"]
        result = submit_intake(
            session,
            payload,
            source=SOURCE,
            event_key=uuid.uuid4(),
            clock=clock,
            routing_config=ROUTING_CONFIG,
        )
        enquiry_id = result.enquiry_id
    finally:
        session.close()

    prepare_jobs = _fetch(
        postgres_db,
        "SELECT id FROM leadops.jobs WHERE kind = 'prepare_enquiry' "
        "AND entity_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )
    assert len(prepare_jobs) == 1
    prepare_job_id = prepare_jobs[0].id

    # Four crashed attempts: claim, then the worker dies before
    # any handling (the lease is expired by SQL between claims),
    # exactly the R9 ceiling drive. The fifth claim closes the
    # job at the budget ceiling instead of stamping a lease.
    for _ in range(4):
        claim = _run_claim_only(postgres_db, clock)
        assert claim is not None
        assert claim.job_id == prepare_job_id
        _execute(
            postgres_db,
            "UPDATE leadops.jobs "
            "SET lease_until = now() - interval '1 second' "
            "WHERE id = :job_id",
            {"job_id": prepare_job_id},
        )
    closing = _run_claim_only(postgres_db, clock)
    assert closing is None

    # Persisted facts: prepare is dead, nothing was prepared, and
    # no sync job was ever created for the contact.
    job = _fetch(
        postgres_db,
        "SELECT state FROM leadops.jobs WHERE id = :job_id",
        {"job_id": prepare_job_id},
    )[0]
    assert job.state == "dead"
    enquiry = _fetch(
        postgres_db,
        "SELECT local_prepared_at FROM leadops.enquiries "
        "WHERE id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]
    assert enquiry.local_prepared_at is None
    assert (
        _fetch(
            postgres_db,
            "SELECT id FROM leadops.jobs WHERE kind = 'sync_contact'",
        )
        == []
    )

    body = _detail(api_client, enquiry_id)
    assert set(body.keys()) == DETAIL_KEYS
    # local_prepare_dead forces the failed summary, and the dead
    # job is an intervention, not a routine review.
    assert body["processing_summary"] == "failed"
    assert body["attention"] == "intervention_required"
    assert body["crm"]["status"] == "pending"
    assert body["crm"]["next_attempt_at"] is None


def _run_claim_only(postgres_db: str, clock: FakeClock):
    session = _factory(postgres_db)()
    try:
        return claim_next(session, WORKER, clock)
    finally:
        session.close()
