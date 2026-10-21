"""S7b red tests: POST /api/v1/intake/form.

Source contracts (transcribed by hand; plan package v3.0):
- IntakeData constraints, the Idempotency-Key (UUID, required)
  and X-CSRF-Token headers, the 202 IntakeReceipt shape and the
  Deduplication enum come from the API contract.
- A07: schema rejection for email, domain rejection (mapped to
  422) for phone, each with field_errors naming the field.

The client fixture is already logged in; database facts are read
back with raw SQL.
"""

from __future__ import annotations

import re
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


def _submit(
    api_client: TestClient,
    payload: dict,
    key: uuid.UUID | None,
    *,
    csrf: bool = True,
):
    headers: dict[str, str] = {}
    if key is not None:
        headers[KEY_HEADER] = str(key)
    if csrf:
        headers[CSRF_HEADER] = api_client.csrf_token  # type: ignore[attr-defined]
    return api_client.post("/api/v1/intake/form", json=payload, headers=headers)


def _counts(postgres_db: str) -> tuple[int, int]:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            enquiries = conn.execute(
                text("SELECT count(*) FROM leadops.enquiries")
            ).scalar_one()
            receipts = conn.execute(
                text("SELECT count(*) FROM leadops.ingestion_receipts")
            ).scalar_one()
        return enquiries, receipts
    finally:
        engine.dispose()


def _error(response) -> dict:
    body = response.json()
    assert "error" in body, f"not an Error envelope: {body!r}"
    return body["error"]


def _field_paths(response) -> list[str]:
    return [f["path"] for f in _error(response)["field_errors"]]


def test_happy_path_returns_202_receipt(
    api_client: TestClient, postgres_db: str
) -> None:
    response = _submit(api_client, ALEX, uuid.uuid4())
    assert response.status_code == 202
    body = response.json()
    receipt_id = uuid.UUID(body["receipt_id"])
    enquiry_id = uuid.UUID(body["enquiry_id"])
    assert re.fullmatch(r"ENQ-[0-9]{6,}", body["display_no"])
    assert body["accepted"] is True
    assert body["replayed"] is False
    assert body["deduplication"] == "new"
    assert body["status_url"]
    assert str(enquiry_id) in body["status_url"]

    assert _counts(postgres_db) == (1, 1)
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            stored = conn.execute(
                text(
                    "SELECT enquiry_id FROM leadops.ingestion_receipts "
                    "WHERE id = :receipt_id"
                ),
                {"receipt_id": receipt_id},
            ).scalar_one()
    finally:
        engine.dispose()
    assert stored == enquiry_id


def test_malformed_email_is_schema_422(
    api_client: TestClient, postgres_db: str
) -> None:
    response = _submit(api_client, {**ALEX, "email": "not-an-email"}, uuid.uuid4())
    assert response.status_code == 422
    assert _error(response)["code"] == "validation_failed"
    assert any("email" in path for path in _field_paths(response))
    assert _counts(postgres_db) == (0, 0)


def test_overlength_email_is_schema_422(
    api_client: TestClient, postgres_db: str
) -> None:
    long_email = "a" * 244 + "@example.com"  # 256 characters
    assert len(long_email) > 254
    response = _submit(api_client, {**ALEX, "email": long_email}, uuid.uuid4())
    assert response.status_code == 422
    assert _error(response)["code"] == "validation_failed"
    assert any("email" in path for path in _field_paths(response))
    assert _counts(postgres_db) == (0, 0)


def test_invalid_phone_is_domain_422(
    api_client: TestClient, postgres_db: str
) -> None:
    # Passes the string schema (19 chars) but fails AU
    # normalization: this must be the domain rejection mapped to
    # 422, never a lucky schema failure.
    response = _submit(
        api_client, {**ALEX, "phone": "+614123456789012345"}, uuid.uuid4()
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "validation_failed"
    assert any("phone" in path for path in _field_paths(response))
    assert _counts(postgres_db) == (0, 0)


def test_replay_returns_original_receipt(
    api_client: TestClient, postgres_db: str
) -> None:
    key = uuid.uuid4()
    first = _submit(api_client, ALEX, key)
    assert first.status_code == 202
    second = _submit(api_client, ALEX, key)
    assert second.status_code == 202

    first_body, second_body = first.json(), second.json()
    assert second_body["replayed"] is True
    assert second_body["receipt_id"] == first_body["receipt_id"]
    assert second_body["enquiry_id"] == first_body["enquiry_id"]
    assert _counts(postgres_db) == (1, 1)


def test_same_key_changed_message_conflicts(
    api_client: TestClient, postgres_db: str
) -> None:
    key = uuid.uuid4()
    assert _submit(api_client, ALEX, key).status_code == 202
    changed = {
        **ALEX,
        "message": "Actually the kitchen needs the work first, please call me.",
    }
    response = _submit(api_client, changed, key)
    assert response.status_code == 409
    assert _error(response)["code"] == "idempotency_conflict"
    assert _counts(postgres_db) == (1, 1)


def test_content_duplicate_shares_enquiry(
    api_client: TestClient, postgres_db: str
) -> None:
    first = _submit(api_client, ALEX, uuid.uuid4())
    assert first.status_code == 202
    second = _submit(api_client, ALEX, uuid.uuid4())
    assert second.status_code == 202

    body = second.json()
    assert body["deduplication"] == "content_duplicate"
    assert body["replayed"] is False
    assert body["enquiry_id"] == first.json()["enquiry_id"]
    assert _counts(postgres_db) == (1, 2)


def test_missing_idempotency_key_is_422(api_client: TestClient) -> None:
    response = _submit(api_client, ALEX, None)
    assert response.status_code == 422
    assert _error(response)["code"] == "validation_failed"
    assert any(
        "idempotency" in path.lower() for path in _field_paths(response)
    )


def test_missing_csrf_is_403_without_rows(
    api_client: TestClient, postgres_db: str
) -> None:
    response = _submit(api_client, ALEX, uuid.uuid4(), csrf=False)
    assert response.status_code == 403
    assert _error(response)["code"] == "csrf_invalid"
    assert _counts(postgres_db) == (0, 0)


def test_no_session_cookie_is_401(api_client: TestClient) -> None:
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    response = anonymous.post(
        "/api/v1/intake/form",
        json=ALEX,
        headers={
            KEY_HEADER: str(uuid.uuid4()),
            CSRF_HEADER: api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert response.status_code == 401
    assert _error(response)["code"] == "unauthenticated"
