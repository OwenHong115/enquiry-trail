"""R6 red tests: replay dedupe type and task event actors.

Two consistency defects:

- A replay answers the hardcoded deduplication "new" instead of
  the type stored on the receipt being replayed, so replaying a
  content_duplicate receipt contradicts its own row.
- Task completed/cancelled events are written with
  actor_id=None even though the acting operator is known, unlike
  review.resolved and integration.resumed, which record it.

Test 3 is a passing guard, declared as such: the resolution note
already agrees across the column, the event and the detail.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

CSRF_HEADER = "X-CSRF-Token"
KEY_HEADER = "Idempotency-Key"
RESOLUTION_NOTE = "Confirmed scope by phone"
COMPLETE_REASON = "Called the customer"
CANCEL_REASON = "Customer withdrew"

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
SAM: dict = {
    **ALEX,
    "first_name": "Sam",
    "last_name": "Lee",
    "email": "sam.lee@example.com",
    "message": "Kitchen quote please, ideally this quarter.",
    "service_type": "kitchen",
}


def _submit(api_client: TestClient, payload: dict, key: uuid.UUID) -> dict:
    response = api_client.post(
        "/api/v1/intake/form",
        json=payload,
        headers={
            KEY_HEADER: str(key),
            CSRF_HEADER: api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _detail(api_client: TestClient, receipt: dict) -> dict:
    response = api_client.get(receipt["status_url"])
    assert response.status_code == 200, response.text
    return response.json()


def _post_action(api_client: TestClient, path: str, body: dict):
    return api_client.post(
        path,
        json=body,
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )


def _events(postgres_db: str, event_type: str, enquiry_id: str) -> list:
    return _fetch(
        postgres_db,
        "SELECT actor_type, actor_id, details_json FROM leadops.activity_events "
        "WHERE event_type = :event_type AND enquiry_id = :enquiry_id",
        {"event_type": event_type, "enquiry_id": uuid.UUID(enquiry_id)},
    )


def test_replay_keeps_stored_dedupe_type(
    api_client: TestClient, postgres_db: str
) -> None:
    key_one = uuid.uuid4()
    key_two = uuid.uuid4()

    first = _submit(api_client, ALEX, key_one)
    assert first["deduplication"] == "new"
    assert first["replayed"] is False

    duplicate = _submit(api_client, ALEX, key_two)
    assert duplicate["deduplication"] == "content_duplicate"
    assert duplicate["replayed"] is False
    assert duplicate["enquiry_id"] == first["enquiry_id"]

    # Replaying the duplicate receipt (same key, same payload)
    # must report the type stored on that receipt.
    replay = _submit(api_client, ALEX, key_two)

    # Persisted truth first: no third receipt was written, and
    # the key_two row really is a content_duplicate.
    receipts = _fetch(
        postgres_db,
        "SELECT event_key, deduplication FROM leadops.ingestion_receipts",
    )
    assert len(receipts) == 2
    by_key = {row.event_key: row.deduplication for row in receipts}
    assert by_key[key_two] == "content_duplicate"
    assert by_key[key_one] == "new"

    assert replay["replayed"] is True
    assert replay["enquiry_id"] == first["enquiry_id"]
    assert replay["receipt_id"] == duplicate["receipt_id"]
    assert replay["deduplication"] == "content_duplicate"

    # The unchanged neighbour: replaying a "new" receipt still
    # reports "new".
    new_replay = _submit(api_client, ALEX, key_one)
    assert new_replay["replayed"] is True
    assert new_replay["enquiry_id"] == first["enquiry_id"]
    assert new_replay["deduplication"] == "new"
    receipts = _fetch(
        postgres_db, "SELECT id FROM leadops.ingestion_receipts"
    )
    assert len(receipts) == 2


def test_task_events_carry_acting_operator(
    api_client: TestClient, api_operator: object, postgres_db: str
) -> None:
    # Complete the first enquiry's follow-up task.
    receipt = _submit(api_client, ALEX, uuid.uuid4())
    task_id = _detail(api_client, receipt)["task"]["id"]
    completed = _post_action(
        api_client,
        f"/api/v1/tasks/{task_id}/complete",
        {"expected_version": 1, "reason": COMPLETE_REASON},
    )
    assert completed.status_code == 200, completed.text

    events = _events(postgres_db, "task.completed", receipt["enquiry_id"])
    assert len(events) == 1
    assert events[0].actor_type == "operator"
    assert events[0].actor_id == api_operator
    assert events[0].details_json["task_id"] == task_id

    # Cancel a second enquiry's task and expect the same.
    second = _submit(api_client, SAM, uuid.uuid4())
    second_task_id = _detail(api_client, second)["task"]["id"]
    cancelled = _post_action(
        api_client,
        f"/api/v1/tasks/{second_task_id}/cancel",
        {"expected_version": 1, "reason": CANCEL_REASON},
    )
    assert cancelled.status_code == 200, cancelled.text

    events = _events(postgres_db, "task.cancelled", second["enquiry_id"])
    assert len(events) == 1
    assert events[0].actor_type == "operator"
    assert events[0].actor_id == api_operator
    assert events[0].details_json["task_id"] == second_task_id


def test_resolution_note_agrees_across_sources(
    api_client: TestClient, api_operator: object, postgres_db: str
) -> None:
    """Expected to PASS today: the resolution note already agrees.

    This guards the already-correct behaviour and is the contrast
    case for the task events in test 2: review.resolved already
    records the acting operator.
    """
    receipt = _submit(api_client, {**ALEX, "postcode": None}, uuid.uuid4())
    enquiry_id = receipt["enquiry_id"]
    detail = _detail(api_client, receipt)
    assert detail["requires_review"] is True

    resolved = _post_action(
        api_client,
        f"/api/v1/enquiries/{enquiry_id}/review/resolve",
        {
            "expected_version": detail["version"],
            "resolution_note": RESOLUTION_NOTE,
        },
    )
    assert resolved.status_code == 200, resolved.text

    row = _fetch(
        postgres_db,
        "SELECT resolution_note FROM leadops.enquiries WHERE id = :enquiry_id",
        {"enquiry_id": uuid.UUID(enquiry_id)},
    )[0]
    assert row.resolution_note == RESOLUTION_NOTE

    events = _events(postgres_db, "review.resolved", enquiry_id)
    assert len(events) == 1
    assert events[0].details_json["resolution_note"] == RESOLUTION_NOTE
    assert events[0].actor_id == api_operator

    detail = _detail(api_client, receipt)
    assert detail["review_resolution_note"] == RESOLUTION_NOTE
