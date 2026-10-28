"""P3b/P3c (A27): DB rate limits and signed webhook intake."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.bootstrap import create_app
from app.db.session import get_session
from app.settings import Settings

INTAKE: dict = {
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
LOGIN = {"username": "intake-operator", "password": "intake-demo-pass"}
SECRET = "whsec-test"


@pytest.fixture(autouse=True)
def _clear_rate_counters(postgres_db: str):
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE leadops.rate_limit_counters"))
    finally:
        engine.dispose()
    yield


def _client(postgres_db: str, **overrides) -> TestClient:
    kwargs = dict(
        database_url=postgres_db,
        app_base_url="http://testserver",
        session_secret="test-session-secret",
        webhook_secret=SECRET,
    )
    kwargs.update(overrides)
    app = create_app(Settings(**kwargs))
    factory = sessionmaker(bind=create_engine(postgres_db))

    def override_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    return TestClient(app, raise_server_exceptions=False)


def _login(client: TestClient):
    return client.post("/api/v1/auth/login", json=LOGIN)


def _count_enquiries(postgres_db: str) -> int:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM leadops.enquiries")).scalar_one()
    finally:
        engine.dispose()


def _sign(body: bytes, timestamp: int, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _webhook(client: TestClient, body: bytes, timestamp: int, signature: str, key: str):
    return client.post(
        "/api/v1/webhooks/intake",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": str(timestamp),
            "X-Webhook-Signature": signature,
            "Idempotency-Key": key,
        },
    )


def test_login_rate_limit_returns_429_envelope(postgres_db: str, api_operator) -> None:
    client = _client(postgres_db, rate_limit_login_max=2, rate_limit_window_seconds=60)
    assert _login(client).status_code == 200
    assert _login(client).status_code == 200
    blocked = _login(client)
    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.headers.get("x-request-id")


def test_intake_rate_limit_returns_429_envelope(postgres_db: str, api_operator) -> None:
    client = _client(postgres_db, rate_limit_intake_max=1, rate_limit_window_seconds=60)
    login = _login(client)
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())}
    assert client.post("/api/v1/intake/form", json=INTAKE, headers=headers).status_code == 202
    blocked = client.post(
        "/api/v1/intake/form", json=INTAKE,
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert blocked.status_code == 429, blocked.text
    assert blocked.json()["error"]["code"] == "rate_limited"


def test_spoofed_xff_does_not_evade_limit(postgres_db: str, api_operator) -> None:
    client = _client(postgres_db, rate_limit_login_max=1, rate_limit_window_seconds=60)
    assert _login(client).status_code == 200
    spoofed = client.post(
        "/api/v1/auth/login", json=LOGIN, headers={"X-Forwarded-For": "198.51.100.99"}
    )
    assert spoofed.status_code == 429, spoofed.text


def test_webhook_valid_creates_enquiry_without_session(postgres_db: str) -> None:
    client = _client(postgres_db)
    body = json.dumps(INTAKE).encode()
    ts = int(time.time())
    response = _webhook(client, body, ts, _sign(body, ts), str(uuid.uuid4()))
    assert response.status_code == 202, response.text
    assert response.json()["accepted"] is True
    assert _count_enquiries(postgres_db) == 1


def test_webhook_bad_signature_creates_nothing(postgres_db: str) -> None:
    client = _client(postgres_db)
    body = json.dumps(INTAKE).encode()
    ts = int(time.time())
    response = _webhook(client, body, ts, "sha256=" + "0" * 64, str(uuid.uuid4()))
    assert response.status_code == 401, response.text
    assert _count_enquiries(postgres_db) == 0


def test_webhook_stale_timestamp_rejected_creates_nothing(postgres_db: str) -> None:
    client = _client(postgres_db)
    body = json.dumps(INTAKE).encode()
    ts = int(time.time()) - 3600
    response = _webhook(client, body, ts, _sign(body, ts), str(uuid.uuid4()))
    assert response.status_code == 401, response.text
    assert _count_enquiries(postgres_db) == 0


def test_webhook_replay_rejected_second_delivery_creates_nothing_new(postgres_db: str) -> None:
    client = _client(postgres_db)
    body = json.dumps(INTAKE).encode()
    ts = int(time.time())
    signature = _sign(body, ts)
    key = str(uuid.uuid4())
    assert _webhook(client, body, ts, signature, key).status_code == 202
    replay = _webhook(client, body, ts, signature, str(uuid.uuid4()))
    assert replay.status_code == 409, replay.text
    assert replay.json()["error"]["code"] == "webhook_replay"
    assert _count_enquiries(postgres_db) == 1


def test_webhook_invalid_payload_does_not_burn_signature(postgres_db: str) -> None:
    client = _client(postgres_db)
    invalid = {**INTAKE, "message": "short"}
    body = json.dumps(invalid).encode()
    ts = int(time.time())
    signature = _sign(body, ts)
    key = str(uuid.uuid4())
    first = _webhook(client, body, ts, signature, key)
    assert first.status_code == 422, first.text
    assert _count_enquiries(postgres_db) == 0
    # Exact redelivery must still be a validation failure, not a
    # webhook replay: the failed attempt must not claim the signature.
    again = _webhook(client, body, ts, signature, key)
    assert again.status_code == 422, again.text
    assert again.json()["error"]["code"] == "validation_failed"
    # A corrected delivery succeeds afterwards. A signature covers
    # its body, so the corrected body is re-signed; the original
    # event key is reused and no enquiry was created before.
    valid_body = json.dumps(INTAKE).encode()
    corrected = _webhook(client, valid_body, ts, _sign(valid_body, ts), key)
    assert corrected.status_code == 202, corrected.text
    assert corrected.json()["accepted"] is True
    assert _count_enquiries(postgres_db) == 1


def test_webhook_fresh_signature_same_event_key_is_intake_replay(postgres_db: str) -> None:
    client = _client(postgres_db)
    body = json.dumps(INTAKE).encode()
    key = str(uuid.uuid4())
    ts1 = int(time.time())
    first = _webhook(client, body, ts1, _sign(body, ts1), key)
    assert first.status_code == 202, first.text
    ts2 = ts1 + 1
    second = _webhook(client, body, ts2, _sign(body, ts2), key)
    assert second.status_code == 202, second.text
    assert second.json()["replayed"] is True
    assert second.json()["enquiry_id"] == first.json()["enquiry_id"]
    assert _count_enquiries(postgres_db) == 1
