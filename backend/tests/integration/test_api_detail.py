"""S7c red tests: GET /api/v1/enquiries/{id}.

The expected detail shape is transcribed by hand from the detail
fixtures (top-level key set only); assertions below pin facts, not
the fixtures' placeholder values.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.worker.claiming import claim_next, handle_claim
from support.fake_crm import FakeCrm
from support.fake_clock import FakeClock

CSRF_HEADER = "X-CSRF-Token"
KEY_HEADER = "Idempotency-Key"
WORKER = uuid.UUID("12121212-1212-1212-1212-121212121212")

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

# Transcribed from the detail fixtures' top-level shape.
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


def _submit(api_client: TestClient, key: uuid.UUID | None = None) -> dict:
    response = api_client.post(
        "/api/v1/intake/form",
        json=ALEX,
        headers={
            KEY_HEADER: str(key or uuid.uuid4()),
            CSRF_HEADER: api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _drain(postgres_db: str, fake: FakeCrm) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    for _ in range(2):
        session = _factory(postgres_db)()
        try:
            claim = claim_next(session, WORKER, clock)
            assert claim is not None
            handle_claim(session, claim, clock, crm=fake)
        finally:
            session.close()


def _get(api_client: TestClient, path: str):
    return api_client.get(path)


def test_ready_detail_matches_fixture_shape(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    fake = FakeCrm()
    _drain(postgres_db, fake)

    response = _get(api_client, receipt["status_url"])
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == DETAIL_KEYS

    assert body["id"] == receipt["enquiry_id"]
    assert body["display_no"] == "ENQ-000001"
    assert body["contact"]["email"] == "alex.demo@example.com"
    assert body["contact"]["first_name"] == "Alex"
    assert body["contact"]["last_name"] == "Morgan"
    assert body["contact"]["phone_e164"] == "+61412345678"
    assert body["owner"]["code"] == "maya"
    assert body["processing_summary"] == "ready"
    assert body["attention"] == "none"
    assert body["requires_review"] is False
    assert body["crm"]["status"] == "synced"
    assert body["crm"]["remote_id"] == fake.create_remote_id
    assert body["crm"]["synced_contact_version"] == 1
    # Mock profile: no real CRM record URL exists.
    assert body["crm"]["remote_url"] is None
    assert body["task"]["kind"] == "initial_follow_up"
    assert body["task"]["owner"]["code"] == "maya"
    assert body["local_prepared_at"] is not None
    assert body["required_contact_version"] == 1
    assert body["draft"] is None
    assert body["enrichment"] is None
    assert body["notification"]["status"] == "disabled"
    related = body["related_jobs"]
    assert len(related) == 2
    assert {job["kind"] for job in related} == {"prepare_enquiry", "sync_contact"}
    assert all(job["state"] == "succeeded" for job in related)
    assert all(set(job) == {"id", "kind", "state", "updated_at"} for job in related)
    assert all(uuid.UUID(job["id"]) for job in related)
    assert all(datetime.fromisoformat(job["updated_at"].replace("Z", "+00:00"))
               for job in related)


def test_processing_detail_right_after_submit(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    response = _get(api_client, receipt["status_url"])
    assert response.status_code == 200
    body = response.json()

    assert body["processing_summary"] == "processing"
    assert body["crm"]["status"] == "pending"
    assert body["local_prepared_at"] is None
    # Known divergence (recorded in AGENTS.md): this build creates
    # owner, routing and the follow-up task in the intake
    # transaction, so they already exist in the processing state.
    # The fixture's null owner/task for this state is not reachable
    # here and is deliberately not reproduced.
    assert body["owner"]["code"] == "maya"
    assert body["task"] is not None
    assert body["task"]["status"] == "open"


def test_unknown_enquiry_is_404_envelope(api_client: TestClient) -> None:
    response = _get(api_client, f"/api/v1/enquiries/{uuid.uuid4()}")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"


def test_malformed_enquiry_id_is_422(api_client: TestClient) -> None:
    response = _get(api_client, "/api/v1/enquiries/not-a-uuid")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    paths = [f["path"] for f in body["error"]["field_errors"]]
    assert any("enquiry_id" in path for path in paths)


def test_detail_requires_live_session_without_csrf(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)

    # Safe method: no CSRF header is needed, the session is enough.
    ok = _get(api_client, receipt["status_url"])
    assert ok.status_code == 200

    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    missing = anonymous.get(receipt["status_url"])
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "unauthenticated"

    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE leadops.sessions SET expires_at = "
                    "now() - interval '1 minute'"
                )
            )
    finally:
        engine.dispose()
    expired = api_client.get(receipt["status_url"])
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "unauthenticated"


def test_duplicate_content_detail_is_canonical(
    api_client: TestClient, postgres_db: str
) -> None:
    first = _submit(api_client)
    second = _submit(api_client)
    assert second["deduplication"] == "content_duplicate"

    response = _get(api_client, first["status_url"])
    assert response.status_code == 200
    assert response.json()["id"] == first["enquiry_id"]

    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            enquiries = conn.execute(
                text("SELECT count(*) FROM leadops.enquiries")
            ).scalar_one()
            tasks = conn.execute(
                text(
                    "SELECT count(*) FROM leadops.follow_up_tasks "
                    "WHERE enquiry_id = :enquiry_id"
                ),
                {"enquiry_id": uuid.UUID(first["enquiry_id"])},
            ).scalar_one()
    finally:
        engine.dispose()
    assert enquiries == 1
    assert tasks == 1
