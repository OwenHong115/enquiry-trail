"""P2 list endpoints: GET /api/v1/enquiries and GET /api/v1/tasks.

Cursor-paginated, authenticated reads sharing the detail status
projection. Assertions are on public outputs only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm

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


def _submit(api_client: TestClient, **overrides) -> dict:
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


def _drain(postgres_db: str, count: int = 2) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    fake = FakeCrm()
    for _ in range(count):
        session = _factory(postgres_db)()
        try:
            claim = claim_next(session, WORKER, clock)
            assert claim is not None
            handle_claim(session, claim, clock, crm=fake)
        finally:
            session.close()


def _sql(postgres_db: str, sql: str, params: dict | None = None) -> None:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


def _list(api_client: TestClient, path: str):
    return api_client.get(path)


def test_enquiries_list_requires_auth(api_client: TestClient) -> None:
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    response = anonymous.get("/api/v1/enquiries")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_tasks_list_requires_auth(api_client: TestClient) -> None:
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    response = anonymous.get("/api/v1/tasks")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_enquiries_list_shape_and_newest_first(
    api_client: TestClient, postgres_db: str
) -> None:
    first = _submit(api_client)
    second = _submit(
        api_client, email="sam.lee@example.com", first_name="Sam",
        message="A different kitchen enquiry with enough detail here.",
    )
    response = _list(api_client, "/api/v1/enquiries")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    ids = [item["id"] for item in body["items"]]
    assert ids == [second["enquiry_id"], first["enquiry_id"]]
    item = body["items"][0]
    assert item["display_no"].startswith("ENQ-")
    assert item["contact"]["email"] == "sam.lee@example.com"
    assert item["contact"]["first_name"] == "Sam"
    assert item["owner"]["code"] == "maya"
    assert item["processing_summary"] == "processing"
    assert item["crm_status"] == "pending"
    assert "next_attempt_at" in item


def test_enquiries_list_cursor_pagination_with_insert_between_pages(
    api_client: TestClient, postgres_db: str
) -> None:
    created = []
    for index in range(5):
        created.append(_submit(
            api_client, email=f"user{index}@example.com",
            message=f"Enquiry number {index} with a sufficiently long message.",
        )["enquiry_id"])
    page1 = _list(api_client, "/api/v1/enquiries?limit=2").json()
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] is not None
    # A new row inserted between pages must not dup or skip.
    _submit(api_client, email="late@example.com",
           message="Late enquiry inserted between pages for paging.")
    page2 = _list(
        api_client,
        f"/api/v1/enquiries?limit=2&cursor={page1['next_cursor']}",
    ).json()
    page3 = _list(
        api_client,
        f"/api/v1/enquiries?limit=2&cursor={page2['next_cursor']}",
    ).json()
    assert page3["next_cursor"] is None
    seen = [i["id"] for i in page1["items"] + page2["items"] + page3["items"]]
    assert len(seen) == len(set(seen)) == 5
    assert set(seen) == set(created)


def test_enquiries_list_invalid_cursor_and_limit(api_client: TestClient) -> None:
    assert _list(api_client, "/api/v1/enquiries?cursor=not-a-cursor").status_code == 422
    assert _list(api_client, "/api/v1/enquiries?cursor=aGVsbG8=").status_code == 422
    assert _list(api_client, "/api/v1/enquiries?limit=0").status_code == 422
    assert _list(api_client, "/api/v1/enquiries?limit=101").status_code == 422
    assert _list(api_client, "/api/v1/enquiries?limit=abc").status_code == 422


def test_enquiries_list_filters(api_client: TestClient, postgres_db: str) -> None:
    maya = _submit(api_client)
    _submit(api_client, email="oli@example.com", postcode="2000",
           message="Inner city general enquiry with enough detail.")
    by_owner = _list(api_client, "/api/v1/enquiries?owner=maya").json()
    assert [i["id"] for i in by_owner["items"]] == [maya["enquiry_id"]]
    by_owner2 = _list(api_client, "/api/v1/enquiries?owner=oliver").json()
    assert len(by_owner2["items"]) == 1
    assert by_owner2["items"][0]["owner"]["code"] == "oliver"
    by_q = _list(api_client, "/api/v1/enquiries?q=oli%40example.com").json()
    assert len(by_q["items"]) == 1
    by_status = _list(
        api_client, "/api/v1/enquiries?processing_summary=processing"
    ).json()
    assert len(by_status["items"]) == 2
    ready = _list(api_client, "/api/v1/enquiries?processing_summary=ready").json()
    assert ready["items"] == []
    for value in ("processing", "waiting_retry", "blocked",
                  "reconciling", "ready", "failed"):
        assert _list(
            api_client, f"/api/v1/enquiries?processing_summary={value}"
        ).status_code == 200
    for value in ("none", "review_required", "retry_scheduled",
                  "notification_uncertain", "integration_blocked",
                  "intervention_required"):
        assert _list(
            api_client, f"/api/v1/enquiries?attention={value}"
        ).status_code == 200
    assert _list(
        api_client, "/api/v1/enquiries?processing_summary=bogus"
    ).status_code == 422
    assert _list(
        api_client, "/api/v1/enquiries?attention=bogus"
    ).status_code == 422


def _set_sync_state(postgres_db: str, enquiry_id: str, state: str) -> None:
    _sql(
        postgres_db,
        "UPDATE leadops.jobs SET state = :state, "
        "next_attempt_at = now() + interval '5 minutes' "
        "WHERE kind = 'sync_contact' AND dedupe_key = "
        "(SELECT 'sync_contact:' || contact_id::text FROM leadops.enquiries "
        "WHERE id = :enquiry_id)",
        {"state": state, "enquiry_id": enquiry_id},
    )


def test_list_detail_status_consistency(api_client: TestClient, postgres_db: str) -> None:
    cases: dict[str, str] = {}

    ready = _submit(api_client, email="ready@example.com",
                    message="Ready state enquiry message body here.")
    _drain(postgres_db)
    cases[ready["enquiry_id"]] = "ready"

    processing = _submit(api_client, email="proc@example.com",
                         message="Processing state enquiry message body.")
    cases[processing["enquiry_id"]] = "processing"

    for state, label in (("waiting_retry", "waiting_retry"),
                         ("reconciling", "reconciling"),
                         ("blocked", "blocked"),
                         ("dead", "dead")):
        receipt = _submit(
            api_client, email=f"{label}@example.com",
            message=f"State {label} enquiry with a long enough message.",
        )
        # Park its prepare so the shared drain below cannot move it,
        # then give it a sync job in the target state directly.
        _sql(postgres_db,
             "INSERT INTO leadops.jobs (kind, entity_id, dedupe_key, state, "
             "next_attempt_at) SELECT 'sync_contact', e.id, "
             "'sync_contact:' || e.contact_id::text, :state, now() "
             "FROM leadops.enquiries e WHERE e.id = :id",
             {"state": state, "id": receipt["enquiry_id"]})
        _sql(postgres_db,
             "UPDATE leadops.enquiries SET local_prepared_at = now() "
             "WHERE id = :id", {"id": receipt["enquiry_id"]})
        cases[receipt["enquiry_id"]] = label

    items = {
        item["id"]: item
        for item in _list(api_client, "/api/v1/enquiries?limit=100").json()["items"]
    }
    for enquiry_id in cases:
        detail = api_client.get(f"/api/v1/enquiries/{enquiry_id}").json()
        item = items[enquiry_id]
        assert item["processing_summary"] == detail["processing_summary"], enquiry_id
        assert item["attention"] == detail["attention"], enquiry_id
        assert item["crm_status"] == detail["crm"]["status"], enquiry_id
    assert items[ready["enquiry_id"]]["processing_summary"] == "ready"
    assert items[processing["enquiry_id"]]["processing_summary"] == "processing"


def test_tasks_list_shape_filters_and_cursor(
    api_client: TestClient, postgres_db: str
) -> None:
    receipts = []
    for index in range(3):
        receipts.append(_submit(
            api_client, email=f"task{index}@example.com",
            message=f"Task enquiry {index} with a sufficiently long message.",
        ))
    body = _list(api_client, "/api/v1/tasks").json()
    assert set(body) == {"items", "next_cursor"}
    assert len(body["items"]) == 3
    item = body["items"][0]
    assert item["status"] == "open"
    assert item["owner"]["code"] == "maya"
    assert item["enquiry"]["contact"]["email"].endswith("@example.com")
    assert item["enquiry"]["display_no"].startswith("ENQ-")

    open_items = _list(api_client, "/api/v1/tasks?status=open").json()
    assert len(open_items["items"]) == 3
    done = _list(api_client, "/api/v1/tasks?status=completed").json()
    assert done["items"] == []
    assert _list(api_client, "/api/v1/tasks?status=bogus").status_code == 422
    assert _list(api_client, "/api/v1/tasks?owner=oliver").json()["items"] == []
    assert len(_list(api_client, "/api/v1/tasks?owner=maya").json()["items"]) == 3

    page1 = _list(api_client, "/api/v1/tasks?limit=2").json()
    assert len(page1["items"]) == 2 and page1["next_cursor"] is not None
    _submit(api_client, email="tasklate@example.com",
           message="Late task enquiry inserted between pages here.")
    page2 = _list(
        api_client, f"/api/v1/tasks?limit=2&cursor={page1['next_cursor']}"
    ).json()
    assert page2["next_cursor"] is None
    seen = [i["id"] for i in page1["items"] + page2["items"]]
    assert len(seen) == len(set(seen)) == 3
    assert _list(api_client, "/api/v1/tasks?cursor=nope").status_code == 422
    assert _list(api_client, "/api/v1/tasks?limit=101").status_code == 422


def test_enquiries_q_matches_public_display_number(
    api_client: TestClient, postgres_db: str
) -> None:
    target = _submit(api_client)
    _submit(api_client, email="other@example.com",
           message="Another enquiry that must not match the q filters.")
    detail = api_client.get(f"/api/v1/enquiries/{target['enquiry_id']}").json()
    display_no = detail["display_no"]
    assert display_no == "ENQ-000001"
    for query in (display_no, "ENQ-1", "0001"):
        body = _list(api_client, f"/api/v1/enquiries?q={query}").json()
        assert [i["id"] for i in body["items"]] == [target["enquiry_id"]], query
    by_email = _list(api_client, "/api/v1/enquiries?q=alex.demo").json()
    assert [i["id"] for i in by_email["items"]] == [target["enquiry_id"]]
    by_name = _list(api_client, "/api/v1/enquiries?q=alex").json()
    assert target["enquiry_id"] in [i["id"] for i in by_name["items"]]
    unknown = _list(api_client, "/api/v1/enquiries?q=ENQ-9999").json()
    assert unknown["items"] == []
    assert unknown["next_cursor"] is None


def test_list_detail_consistency_review_required(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client, postcode=None)
    detail = api_client.get(f"/api/v1/enquiries/{receipt['enquiry_id']}").json()
    assert detail["requires_review"] is True
    assert detail["attention"] == "review_required"
    items = _list(
        api_client, "/api/v1/enquiries?attention=review_required"
    ).json()["items"]
    assert [i["id"] for i in items] == [receipt["enquiry_id"]]
    assert items[0]["processing_summary"] == detail["processing_summary"]
    assert items[0]["crm_status"] == detail["crm"]["status"]


def test_list_detail_consistency_provider_blocked(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    _sql(
        postgres_db,
        "INSERT INTO leadops.integration_health (provider, status, updated_at) "
        "VALUES ('hubspot', 'blocked', now())",
    )
    detail = api_client.get(f"/api/v1/enquiries/{receipt['enquiry_id']}").json()
    assert detail["attention"] == "integration_blocked"
    items = _list(
        api_client, "/api/v1/enquiries?attention=integration_blocked"
    ).json()["items"]
    assert [i["id"] for i in items] == [receipt["enquiry_id"]]
    assert items[0]["processing_summary"] == detail["processing_summary"]
    assert items[0]["attention"] == detail["attention"]
    assert items[0]["crm_status"] == detail["crm"]["status"]


def test_tasks_overdue_filter(api_client: TestClient, postgres_db: str) -> None:
    receipt = _submit(api_client)
    _sql(postgres_db,
         "UPDATE leadops.follow_up_tasks SET due_at = now() - interval '1 hour' "
         "WHERE enquiry_id = :id", {"id": receipt["enquiry_id"]})
    overdue = _list(api_client, "/api/v1/tasks?overdue=true").json()
    assert len(overdue["items"]) == 1
    assert overdue["items"][0]["enquiry"]["id"] == receipt["enquiry_id"]
    _submit(api_client, email="fresh@example.com",
            message="Fresh task that is not overdue at all here.")
    overdue2 = _list(api_client, "/api/v1/tasks?overdue=true").json()
    assert len(overdue2["items"]) == 1
