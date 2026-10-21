"""S7d red tests: operator write actions over HTTP.

Review resolution and the task terminal actions already have pinned
service semantics (S4a); these tests pin their HTTP boundary: the
EnquiryDetail and Task response shapes, session/CSRF enforcement,
and the conflict mapping (VersionConflict -> 409 version_conflict,
TerminalConflict -> 409 invalid_state).

Note on review_resolved_at: the EnquiryDetail contract carries no
such field (its key set is pinned by the S7c detail tests), so the
persisted column is asserted directly against the database.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

CSRF_HEADER = "X-CSRF-Token"
KEY_HEADER = "Idempotency-Key"

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

# Transcribed from the detail fixtures' top-level shape (see S7c).
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

# Transcribed from the Task schema in the OpenAPI contract.
TASK_KEYS = {
    "id",
    "enquiry_id",
    "kind",
    "title",
    "owner",
    "status",
    "due_at",
    "completed_at",
    "cancelled_at",
    "version",
}

RESOLUTION_NOTE = "Confirmed scope by phone"
COMPLETE_REASON = "Called the customer"
CANCEL_REASON = "Customer withdrew"


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


def _detail(api_client: TestClient, receipt: dict) -> dict:
    response = api_client.get(receipt["status_url"])
    assert response.status_code == 200, response.text
    return response.json()


def _post_action(
    api_client: TestClient,
    path: str,
    body: dict,
    *,
    csrf: bool = True,
):
    headers = {}
    if csrf:
        headers[CSRF_HEADER] = api_client.csrf_token  # type: ignore[attr-defined]
    return api_client.post(path, json=body, headers=headers)


def _resolve(api_client: TestClient, enquiry_id: str, **kwargs: object):
    return _post_action(
        api_client,
        f"/api/v1/enquiries/{enquiry_id}/review/resolve",
        {"expected_version": 1, "resolution_note": RESOLUTION_NOTE},
        **kwargs,  # type: ignore[arg-type]
    )


def _fetch_one(postgres_db: str, sql: str, params: dict | None = None):
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).one()
    finally:
        engine.dispose()


def _enquiry_row(postgres_db: str, enquiry_id: str):
    return _fetch_one(
        postgres_db,
        "SELECT requires_review, review_resolved_at, review_resolved_by, "
        "version FROM leadops.enquiries WHERE id = :enquiry_id",
        {"enquiry_id": uuid.UUID(enquiry_id)},
    )


def _task_row(postgres_db: str, task_id: str):
    return _fetch_one(
        postgres_db,
        "SELECT status, completed_at, cancelled_at, version "
        "FROM leadops.follow_up_tasks WHERE id = :task_id",
        {"task_id": uuid.UUID(task_id)},
    )


def test_review_resolve_returns_detail_and_persists(
    api_client: TestClient, postgres_db: str, api_operator: object
) -> None:
    # R01: a null postcode routes to intake with review required.
    receipt = _submit(api_client, postcode=None)
    enquiry_id = receipt["enquiry_id"]
    before = _detail(api_client, receipt)
    assert before["requires_review"] is True
    assert before["review_reason_codes"] == ["missing_postcode"]

    response = _resolve(api_client, enquiry_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body.keys()) == DETAIL_KEYS
    assert body["id"] == enquiry_id
    assert body["requires_review"] is False
    assert body["version"] == 2
    assert body["review_resolution_note"] == RESOLUTION_NOTE
    # Reason history is retained; resolution never moves the owner.
    assert body["review_reason_codes"] == ["missing_postcode"]
    assert body["owner"]["code"] == "intake"

    row = _enquiry_row(postgres_db, enquiry_id)
    assert row.requires_review is False
    assert row.review_resolved_at is not None
    assert row.review_resolved_by == api_operator
    assert row.version == 2


def test_stale_review_resolve_is_409_version_conflict(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client, postcode=None)
    enquiry_id = receipt["enquiry_id"]
    first = _resolve(api_client, enquiry_id)
    assert first.status_code == 200, first.text

    stale = _resolve(api_client, enquiry_id)
    assert stale.status_code == 409, stale.text
    body = stale.json()
    assert body["error"]["code"] == "version_conflict"
    assert body["error"]["request_id"]

    row = _enquiry_row(postgres_db, enquiry_id)
    assert row.version == 2
    assert _detail(api_client, receipt)["version"] == 2


def test_review_resolve_missing_csrf_is_403_and_no_change(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client, postcode=None)
    enquiry_id = receipt["enquiry_id"]

    response = _resolve(api_client, enquiry_id, csrf=False)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "csrf_invalid"

    row = _enquiry_row(postgres_db, enquiry_id)
    assert row.requires_review is True
    assert row.review_resolved_at is None
    assert row.version == 1


def test_task_complete_returns_task_shape(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    detail = _detail(api_client, receipt)
    task = detail["task"]
    assert task is not None

    response = _post_action(
        api_client,
        f"/api/v1/tasks/{task['id']}/complete",
        {"expected_version": 1, "reason": COMPLETE_REASON},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body.keys()) == TASK_KEYS
    assert body["id"] == task["id"]
    assert body["enquiry_id"] == receipt["enquiry_id"]
    assert body["kind"] == "initial_follow_up"
    assert body["status"] == "completed"
    assert body["completed_at"] is not None
    assert body["cancelled_at"] is None
    assert body["version"] == 2

    row = _task_row(postgres_db, task["id"])
    assert row.status == "completed"
    assert row.completed_at is not None
    assert row.version == 2


def test_repeat_complete_returns_original_result(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    task_id = _detail(api_client, receipt)["task"]["id"]
    path = f"/api/v1/tasks/{task_id}/complete"

    first = _post_action(
        api_client, path, {"expected_version": 1, "reason": COMPLETE_REASON}
    )
    assert first.status_code == 200, first.text
    completed_at = first.json()["completed_at"]

    # The repeat-terminal rule survives HTTP: a stale expected
    # version still returns the original result, with no new write.
    repeat = _post_action(
        api_client, path, {"expected_version": 1, "reason": COMPLETE_REASON}
    )
    assert repeat.status_code == 200, repeat.text
    body = repeat.json()
    assert body["status"] == "completed"
    assert body["completed_at"] == completed_at
    assert body["version"] == 2

    row = _task_row(postgres_db, task_id)
    assert row.version == 2


def test_cancel_after_completion_is_409_invalid_state(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    task_id = _detail(api_client, receipt)["task"]["id"]

    completed = _post_action(
        api_client,
        f"/api/v1/tasks/{task_id}/complete",
        {"expected_version": 1, "reason": COMPLETE_REASON},
    )
    assert completed.status_code == 200, completed.text

    response = _post_action(
        api_client,
        f"/api/v1/tasks/{task_id}/cancel",
        {"expected_version": 2, "reason": CANCEL_REASON},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "invalid_state"

    row = _task_row(postgres_db, task_id)
    assert row.status == "completed"
    assert row.completed_at is not None
    assert row.cancelled_at is None
    assert row.version == 2


def test_task_cancel_then_complete_conflicts_and_repeat_cancel_returns_original(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(
        api_client,
        email="sam.lee@example.com",
        message="Kitchen quote please, ideally this quarter.",
        service_type="kitchen",
    )
    task_id = _detail(api_client, receipt)["task"]["id"]
    cancel_path = f"/api/v1/tasks/{task_id}/cancel"

    cancelled = _post_action(
        api_client,
        cancel_path,
        {"expected_version": 1, "reason": CANCEL_REASON},
    )
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert set(body.keys()) == TASK_KEYS
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None
    assert body["completed_at"] is None
    assert body["version"] == 2
    cancelled_at = body["cancelled_at"]

    complete = _post_action(
        api_client,
        f"/api/v1/tasks/{task_id}/complete",
        {"expected_version": 2, "reason": COMPLETE_REASON},
    )
    assert complete.status_code == 409, complete.text
    assert complete.json()["error"]["code"] == "invalid_state"

    # A repeat cancel with a stale version returns the original.
    repeat = _post_action(
        api_client,
        cancel_path,
        {"expected_version": 1, "reason": CANCEL_REASON},
    )
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["cancelled_at"] == cancelled_at
    assert repeat.json()["version"] == 2

    row = _task_row(postgres_db, task_id)
    assert row.status == "cancelled"
    assert row.version == 2


def test_task_complete_without_session_is_401(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    task_id = _detail(api_client, receipt)["task"]["id"]

    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    response = anonymous.post(
        f"/api/v1/tasks/{task_id}/complete",
        json={"expected_version": 1, "reason": COMPLETE_REASON},
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthenticated"

    row = _task_row(postgres_db, task_id)
    assert row.status == "open"
    assert row.version == 1


def test_task_action_short_reason_is_422(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    task_id = _detail(api_client, receipt)["task"]["id"]

    response = _post_action(
        api_client,
        f"/api/v1/tasks/{task_id}/complete",
        {"expected_version": 1, "reason": "short"},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    paths = [f["path"] for f in body["error"]["field_errors"]]
    assert any("reason" in path for path in paths)

    row = _task_row(postgres_db, task_id)
    assert row.status == "open"
    assert row.version == 1
