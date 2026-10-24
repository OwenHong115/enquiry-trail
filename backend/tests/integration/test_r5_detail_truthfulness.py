"""R5 red tests: the enquiry detail must tell the truth.

build_enquiry_detail currently renders a binary ready/processing
summary, a hardcoded attention of "none", and a crm block that
only ever reflects a successful link. Blocked, waiting_retry and
reconciling sync jobs are therefore invisible in the detail.

The vocabulary comes from the schema's job-state values:
processing_summary and crm.status reuse the sync job state
verbatim, crm.next_attempt_at exposes the job's retry instant,
and attention ranks blocked > review_required > reconciling >
retry_scheduled > none. An unresolved review stays visible even
once processing is ready; only review-resolve clears it.

Every scenario asserts the persisted rows first, so the test
proves the API agrees with the database, not with itself.
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
WORKER = uuid.UUID("34343434-3434-3434-3434-343434343434")

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


def _sync_job(postgres_db: str, enquiry_id: str):
    rows = _fetch(
        postgres_db,
        "SELECT state, next_attempt_at, last_error_code FROM leadops.jobs "
        "WHERE kind = 'sync_contact' AND entity_id = :enquiry_id",
        {"enquiry_id": uuid.UUID(enquiry_id)},
    )
    assert len(rows) == 1
    return rows[0]


def _enquiry(postgres_db: str, enquiry_id: str):
    return _fetch(
        postgres_db,
        "SELECT requires_review, review_resolved_at, local_prepared_at "
        "FROM leadops.enquiries WHERE id = :enquiry_id",
        {"enquiry_id": uuid.UUID(enquiry_id)},
    )[0]


def _parse_instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_blocked_detail_outranks_review(
    api_client: TestClient, postgres_db: str
) -> None:
    # A null postcode routes to review; the sync then hits a dead
    # credential and blocks the provider.
    receipt = _submit(api_client, postcode=None)
    enquiry_id = receipt["enquiry_id"]
    clock = FakeClock(datetime.now(timezone.utc))

    prepare = _run_next(postgres_db, clock, FakeCrm())
    assert prepare.kind == "prepare_enquiry"
    failing = FakeCrm(
        lookup_script=[ProviderError("auth_failed", http_status=401)]
    )
    sync = _run_next(postgres_db, clock, failing)
    assert sync.kind == "sync_contact"

    # Persisted truth first.
    job = _sync_job(postgres_db, enquiry_id)
    assert job.state == "blocked"
    assert job.last_error_code == "auth_failed"
    health = _fetch(
        postgres_db,
        "SELECT status FROM leadops.integration_health "
        "WHERE provider = 'hubspot'",
    )
    assert len(health) == 1
    assert health[0].status == "blocked"
    assert _enquiry(postgres_db, enquiry_id).requires_review is True
    links = _fetch(postgres_db, "SELECT id FROM leadops.crm_links")
    assert links == []

    body = _detail(api_client, receipt)
    # Blocked is the truth, and it outranks the pending review.
    assert body["processing_summary"] == "blocked"
    assert body["attention"] == "integration_blocked"
    assert body["crm"]["status"] == "blocked"
    assert body["crm"]["next_attempt_at"] is None
    assert body["requires_review"] is True


def test_waiting_retry_detail_then_recovers_to_ready(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    enquiry_id = receipt["enquiry_id"]
    clock = FakeClock(datetime.now(timezone.utc))
    t0 = clock.now_utc()

    prepare = _run_next(postgres_db, clock, FakeCrm())
    assert prepare.kind == "prepare_enquiry"
    failing = FakeCrm(
        lookup_script=[ProviderError("unavailable", http_status=503)]
    )
    sync = _run_next(postgres_db, clock, failing)
    assert sync.kind == "sync_contact"

    # Persisted truth first: first retryable failure is due at
    # T0 + 1s under the deterministic scheduler.
    job = _sync_job(postgres_db, enquiry_id)
    assert job.state == "waiting_retry"
    assert job.next_attempt_at == t0 + timedelta(seconds=1)

    body = _detail(api_client, receipt)
    assert body["processing_summary"] == "waiting_retry"
    assert body["attention"] == "retry_scheduled"
    assert body["crm"]["status"] == "waiting_retry"
    assert body["crm"]["next_attempt_at"] is not None
    assert _parse_instant(body["crm"]["next_attempt_at"]) == job.next_attempt_at

    # Past the retry instant, a healthy sync completes the work.
    clock.advance_by(2)
    recovered = _run_next(postgres_db, clock, FakeCrm())
    assert recovered.kind == "sync_contact"
    job = _sync_job(postgres_db, enquiry_id)
    assert job.state == "succeeded"
    links = _fetch(
        postgres_db,
        "SELECT sync_status FROM leadops.crm_links",
    )
    assert len(links) == 1
    assert links[0].sync_status == "synced"

    body = _detail(api_client, receipt)
    assert body["processing_summary"] == "ready"
    assert body["attention"] == "none"
    assert body["crm"]["status"] == "synced"
    assert body["crm"]["next_attempt_at"] is None


def test_unresolved_review_stays_visible_until_resolved(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client, postcode=None)
    enquiry_id = receipt["enquiry_id"]

    # Nothing drained yet: the review is already outstanding.
    assert _enquiry(postgres_db, enquiry_id).requires_review is True
    sync_jobs = _fetch(
        postgres_db,
        "SELECT id FROM leadops.jobs WHERE kind = 'sync_contact' "
        "AND entity_id = :enquiry_id",
        {"enquiry_id": uuid.UUID(enquiry_id)},
    )
    assert sync_jobs == []

    body = _detail(api_client, receipt)
    assert body["requires_review"] is True
    assert body["attention"] == "review_required"
    assert body["processing_summary"] == "processing"
    assert body["crm"]["status"] == "pending"

    # Prepare and sync complete the processing, but they cannot
    # resolve a review: it must stay visible on a ready enquiry.
    clock = FakeClock(datetime.now(timezone.utc))
    healthy = FakeCrm()
    _run_next(postgres_db, clock, healthy)
    _run_next(postgres_db, clock, healthy)

    assert _enquiry(postgres_db, enquiry_id).requires_review is True
    assert _sync_job(postgres_db, enquiry_id).state == "succeeded"

    body = _detail(api_client, receipt)
    assert body["processing_summary"] == "ready"
    assert body["crm"]["status"] == "synced"
    assert body["requires_review"] is True
    assert body["attention"] == "review_required"

    # Only the operator's resolve clears it.
    resolved = api_client.post(
        f"/api/v1/enquiries/{enquiry_id}/review/resolve",
        json={
            "expected_version": body["version"],
            "resolution_note": "Confirmed scope by phone",
        },
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )
    assert resolved.status_code == 200, resolved.text

    enquiry = _enquiry(postgres_db, enquiry_id)
    assert enquiry.requires_review is False
    assert enquiry.review_resolved_at is not None

    body = _detail(api_client, receipt)
    assert body["requires_review"] is False
    assert body["attention"] == "none"
    assert body["processing_summary"] == "ready"
