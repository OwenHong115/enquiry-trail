"""R11 red tests: dead and shared sync jobs in the enquiry detail.

build_enquiry_detail (app/api/enquiries.py) tells the truth about
blocked, waiting_retry and reconciling sync jobs (R5), but three
gaps remain:

1. attention has no branch for a dead sync job, so an enquiry
   whose CRM sync ended terminally reports attention "none" (or a
   lower-ranked review) while processing_summary says "dead".
2. When no crm_links row exists, CrmOut.last_error_code is
   hardcoded None, even though the job row carries the provider
   error code that ended it.
3. enquiry_repo.get_sync_job resolves the sync job by
   entity_id = enquiry_id, but a sync job is shared per contact
   (jobs.dedupe_key = "sync_contact:<contact_id>"), so a second
   enquiry for the same contact finds no job at all and renders
   as untouched "processing" while the shared job is retrying.

Vocabulary pinned for green (extending the R5 rules):

- attention precedence: blocked > dead > review_required >
  reconciling > retry_scheduled > none. A dead sync outranks a
  routine review because the contact never reached the CRM.
- crm.last_error_code: the crm_links value when a link row
  exists; otherwise the sync job's last_error_code; otherwise
  null.

related_jobs is deliberately not asserted: it stays [] this
round. Every scenario asserts the persisted rows first, so the
test proves the API agrees with the database, not with itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import ProviderError
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler

CSRF_HEADER = "X-CSRF-Token"
KEY_HEADER = "Idempotency-Key"
WORKER = uuid.UUID("11111111-2222-3333-4444-555555555555")

ALEX: dict = {
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

# Transcribed from the detail fixtures' top-level shape; the
# 26-key shape must not change.
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


def _submit(api_client: TestClient, **overrides: object) -> dict:
    payload = {**ALEX, **overrides}
    response = api_client.post(
        "/api/v1/intake/form",
        json=payload,
        headers={
            KEY_HEADER: str(uuid.uuid4()),
            CSRF_HEADER: api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


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


def _detail(api_client: TestClient, receipt: dict) -> dict:
    response = api_client.get(receipt["status_url"])
    assert response.status_code == 200, response.text
    return response.json()


def _sync_jobs(postgres_db: str) -> list:
    return _fetch(
        postgres_db,
        "SELECT id, entity_id, state, next_attempt_at, last_error_code "
        "FROM leadops.jobs WHERE kind = 'sync_contact'",
    )


def _parse_instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_dead_sync_is_visible_and_diagnosable(
    api_client: TestClient, postgres_db: str
) -> None:
    # A null postcode routes to review.
    receipt = _submit(api_client, postcode=None)
    enquiry_id = receipt["enquiry_id"]
    clock = FakeClock(datetime.now(timezone.utc))

    prepare = _run_next(postgres_db, clock, FakeCrm())
    assert prepare.kind == "prepare_enquiry"
    sync = _run_next(
        postgres_db,
        clock,
        FakeCrm(create_error=ProviderError("validation_failed")),
    )
    assert sync.kind == "sync_contact"

    # Persisted facts first: the R10 matrix made this job dead,
    # the review is outstanding, and no link was ever written.
    jobs = _sync_jobs(postgres_db)
    assert len(jobs) == 1
    assert jobs[0].state == "dead"
    assert jobs[0].last_error_code == "validation_failed"
    enquiry = _fetch(
        postgres_db,
        "SELECT requires_review FROM leadops.enquiries WHERE id = :id",
        {"id": uuid.UUID(enquiry_id)},
    )[0]
    assert enquiry.requires_review is True
    assert _fetch(postgres_db, "SELECT id FROM leadops.crm_links") == []

    body = _detail(api_client, receipt)
    assert set(body.keys()) == DETAIL_KEYS
    assert body["processing_summary"] == "failed"
    # Dead outranks the routine review: the contact never
    # reached the CRM.
    assert body["attention"] == "needs_intervention"
    assert body["crm"]["status"] == "failed"
    # No link row exists, so the job's error code is the diagnosis.
    assert body["crm"]["last_error_code"] == "validation_failed"
    assert body["requires_review"] is True


def test_shared_sync_job_is_visible_from_second_enquiry(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt_a = _submit(api_client)
    clock = FakeClock(datetime.now(timezone.utc))
    prepare_a = _run_next(postgres_db, clock, FakeCrm())
    assert prepare_a.kind == "prepare_enquiry"

    # Same email, different content: a new enquiry, not a
    # content duplicate, on the same contact.
    receipt_b = _submit(
        api_client,
        service_type="kitchen",
        message="A different kitchen enquiry, also with enough detail.",
    )
    assert receipt_b["enquiry_id"] != receipt_a["enquiry_id"]
    assert receipt_b["deduplication"] == "new"

    # Claim B's prepare next: park the shared sync job in the
    # future so it cannot be claimed ahead of the prepare.
    shared = _sync_jobs(postgres_db)
    assert len(shared) == 1
    _execute(
        postgres_db,
        "UPDATE leadops.jobs "
        "SET next_attempt_at = next_attempt_at + interval '1 hour' "
        "WHERE id = :job_id",
        {"job_id": shared[0].id},
    )
    clock.advance_to(datetime.now(timezone.utc))
    prepare_b = _run_next(postgres_db, clock, FakeCrm())
    assert prepare_b.kind == "prepare_enquiry"
    assert prepare_b.entity_id == uuid.UUID(receipt_b["enquiry_id"])

    # The shared-job fact this test depends on: still exactly one
    # sync job, and it belongs to enquiry A.
    shared = _sync_jobs(postgres_db)
    assert len(shared) == 1
    assert shared[0].entity_id == uuid.UUID(receipt_a["enquiry_id"])
    job_id = shared[0].id

    # Fail the shared job retryably so it waits with a known
    # next_attempt_at.
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET next_attempt_at = :when "
        "WHERE id = :job_id",
        {"when": clock.now_utc() - timedelta(seconds=1), "job_id": job_id},
    )
    sync = _run_next(
        postgres_db,
        clock,
        FakeCrm(create_error=ProviderError("unavailable")),
    )
    assert sync.job_id == job_id

    job = _sync_jobs(postgres_db)[0]
    assert job.state == "waiting_retry"
    assert job.next_attempt_at is not None

    body_a = _detail(api_client, receipt_a)
    assert body_a["processing_summary"] == "waiting_retry"
    assert body_a["attention"] == "retry_scheduled"
    assert _parse_instant(body_a["crm"]["next_attempt_at"]) == job.next_attempt_at

    # B shares A's sync job through the contact, so B's detail
    # must report the same shared state. Today it renders
    # "processing" / "pending" / "none" because the job lookup
    # by enquiry id misses the shared job entirely.
    body_b = _detail(api_client, receipt_b)
    assert body_b["processing_summary"] == "waiting_retry"
    assert body_b["crm"]["status"] == "waiting_retry"
    assert body_b["attention"] == "retry_scheduled"
    assert _parse_instant(body_b["crm"]["next_attempt_at"]) == job.next_attempt_at
