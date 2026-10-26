"""P3a ingress hardening: exact Origin, body bound, envelope shape."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.bootstrap import create_app
from app.db.session import get_session
from app.settings import Settings
from support.fake_crm import FakeCrm

ORIGIN = "http://testserver"
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


def _intake(api_client: TestClient, origin: str | None = "absent"):
    headers = {
        "Idempotency-Key": str(uuid.uuid4()),
        "X-CSRF-Token": api_client.csrf_token,  # type: ignore[attr-defined]
    }
    if origin != "absent":
        headers["Origin"] = origin  # type: ignore[assignment]
    return api_client.post("/api/v1/intake/form", json=INTAKE, headers=headers)


def test_login_correct_origin_accepted_wrong_rejected(
    api_client: TestClient,
) -> None:
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    ok = anonymous.post(
        "/api/v1/auth/login", json=LOGIN, headers={"Origin": ORIGIN}
    )
    assert ok.status_code == 200, ok.text
    bad = anonymous.post(
        "/api/v1/auth/login", json=LOGIN,
        headers={"Origin": "https://evil.example.com"},
    )
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "origin_invalid"
    assert bad.headers.get("x-request-id")
    assert bad.json()["error"]["request_id"]


def test_login_absent_origin_and_null_origin(api_client: TestClient) -> None:
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    ok = anonymous.post("/api/v1/auth/login", json=LOGIN)
    assert ok.status_code == 200
    null_origin = anonymous.post(
        "/api/v1/auth/login", json=LOGIN, headers={"Origin": "null"}
    )
    assert null_origin.status_code == 403
    assert null_origin.json()["error"]["code"] == "origin_invalid"


def test_get_with_wrong_origin_still_allowed(api_client: TestClient) -> None:
    response = api_client.get(
        "/api/v1/enquiries", headers={"Origin": "https://evil.example.com"}
    )
    assert response.status_code == 200


def test_intake_origin_enforced(api_client: TestClient) -> None:
    ok = _intake(api_client, ORIGIN)
    assert ok.status_code == 202, ok.text
    bad = _intake(api_client, "https://evil.example.com")
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "origin_invalid"


def test_resume_origin_enforced(api_client: TestClient, postgres_db: str) -> None:
    api_client.app.state.crm = FakeCrm(probe_ok=True)
    response = api_client.post(
        "/api/v1/integrations/hubspot/resume",
        json={
            "expected_status": "blocked",
            "expected_updated_at": "2026-01-01T00:00:00Z",
            "reason": "Provider confirmed healthy again",
        },
        headers={
            "X-CSRF-Token": api_client.csrf_token,  # type: ignore[attr-defined]
            "Origin": "https://evil.example.com",
        },
    )
    # Origin rejection happens before any resume state handling.
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_invalid"


def _limited_client(postgres_db: str, api_client: TestClient) -> TestClient:
    settings = Settings(
        database_url=postgres_db,
        app_base_url="http://testserver",
        session_secret="test-session-secret",
        max_request_bytes=1024,
    )
    app = create_app(settings)
    factory = sessionmaker(bind=create_engine(postgres_db))

    def override_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    client = TestClient(app, raise_server_exceptions=False)
    login = client.post("/api/v1/auth/login", json=LOGIN)
    assert login.status_code == 200, login.text
    client.csrf_token = login.json()["csrf_token"]  # type: ignore[attr-defined]
    return client


def test_body_under_limit_accepted(api_client: TestClient, postgres_db: str) -> None:
    client = _limited_client(postgres_db, api_client)
    # Login already proved a small body passes; intake is ~350 bytes.
    response = client.post(
        "/api/v1/intake/form",
        json=INTAKE,
        headers={
            "Idempotency-Key": str(uuid.uuid4()),
            "X-CSRF-Token": client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert response.status_code == 202, response.text


def test_body_over_limit_content_length_413(
    api_client: TestClient, postgres_db: str
) -> None:
    client = _limited_client(postgres_db, api_client)
    big = {**INTAKE, "message": "x" * 2000}
    response = client.post(
        "/api/v1/intake/form",
        json=big,
        headers={
            "Idempotency-Key": str(uuid.uuid4()),
            "X-CSRF-Token": client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers.get("x-request-id")
    assert response.json()["error"]["request_id"]


def test_body_chunked_over_limit_413(
    api_client: TestClient, postgres_db: str
) -> None:
    client = _limited_client(postgres_db, api_client)

    def chunks():
        yield b'{"username": "intake-operator", "password": "'
        yield b"x" * 2000
        yield b'"}'

    response = client.post(
        "/api/v1/auth/login",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers.get("x-request-id")
