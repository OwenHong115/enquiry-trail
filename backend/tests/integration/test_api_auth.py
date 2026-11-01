"""S7a red tests: API session auth (login, session, logout, CSRF).

Source contracts (transcribed by hand; plan package v3.0):
- Paths, LoginInput limits (password 12..256), the Session shape
  and the Error envelope ({error: {code, message, field_errors,
  request_id, retry_after_seconds}}) come from the API contract.
- Cookie name leadops_session, header X-CSRF-Token, 8-hour
  sessions and hashed CSRF tokens come from the security contract.

The app runs in-process via TestClient against the harness test
database; get_session is overridden onto that database. No network
and no real server are involved.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.bootstrap import create_app
from app.db.session import get_session
from app.security.passwords import hash_password
from app.settings import Settings

USERNAME = "api-operator"
PASSWORD = "trailhead-demo-pass"
COOKIE_NAME = "leadops_session"
CSRF_HEADER = "X-CSRF-Token"


@pytest.fixture()
def seeded_api_operator(postgres_db: str) -> uuid.UUID:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO leadops.operators (username, password_hash) "
                    "VALUES (:username, :password_hash) "
                    "ON CONFLICT (username) DO NOTHING"
                ),
                {"username": USERNAME, "password_hash": hash_password(PASSWORD)},
            )
            operator_id = conn.execute(
                text(
                    "SELECT id FROM leadops.operators WHERE username = :username"
                ),
                {"username": USERNAME},
            ).scalar_one()
    finally:
        engine.dispose()
    return operator_id


@pytest.fixture()
def client(postgres_db: str, seeded_api_operator: uuid.UUID) -> TestClient:
    settings = Settings(
        database_url=postgres_db,
        app_base_url="http://testserver",
        session_secret="test-session-secret",
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
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _login(client: TestClient, password: str = PASSWORD):
    return client.post(
        "/api/v1/auth/login",
        json={"username": USERNAME, "password": password},
    )


def _error(response) -> dict:
    body = response.json()
    assert "error" in body, f"not an Error envelope: {body!r}"
    return body["error"]


def test_health_live_returns_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_success_issues_session_and_cookie(
    client: TestClient, seeded_api_operator: uuid.UUID
) -> None:
    response = _login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["operator_id"] == str(seeded_api_operator)
    assert body["username"] == USERNAME
    assert isinstance(body["csrf_token"], str)
    assert len(body["csrf_token"]) >= 32
    assert body["expires_at"]

    set_cookie = response.headers.get("set-cookie", "")
    assert COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie


def test_login_wrong_password_and_unknown_user_look_identical(
    client: TestClient,
) -> None:
    wrong = _login(client, password="wrong-password-123")
    assert wrong.status_code == 401
    wrong_error = _error(wrong)
    assert wrong_error["code"] == "invalid_credentials"

    unknown = client.post(
        "/api/v1/auth/login",
        json={"username": "no-such-user", "password": PASSWORD},
    )
    assert unknown.status_code == 401
    unknown_error = _error(unknown)
    assert unknown_error["code"] == "invalid_credentials"

    # No user enumeration: the two envelopes are shaped identically.
    assert set(wrong_error.keys()) == set(unknown_error.keys())
    assert wrong_error["message"] == unknown_error["message"]


def test_login_short_password_is_422_with_field_errors(
    client: TestClient,
) -> None:
    response = _login(client, password="short-pass1")  # 11 chars
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "validation_failed"
    paths = [field["path"] for field in error["field_errors"]]
    assert any("password" in path for path in paths)


def test_session_roundtrip_and_unauthenticated(client: TestClient) -> None:
    login = _login(client)
    assert login.status_code == 200
    csrf_token = login.json()["csrf_token"]
    operator_id = login.json()["operator_id"]

    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["operator_id"] == operator_id
    assert session.json()["csrf_token"] == csrf_token
    assert session.json()["username"] == USERNAME

    anonymous = TestClient(client.app, raise_server_exceptions=False)
    missing = anonymous.get("/api/v1/auth/session")
    assert missing.status_code == 401
    assert _error(missing)["code"] == "unauthenticated"


def test_logout_requires_csrf_and_revokes(client: TestClient) -> None:
    login = _login(client)
    assert login.status_code == 200
    csrf_token = login.json()["csrf_token"]

    forbidden = client.post("/api/v1/auth/logout")
    assert forbidden.status_code == 403
    assert _error(forbidden)["code"] == "csrf_invalid"
    # The failed logout did not revoke anything.
    assert client.get("/api/v1/auth/session").status_code == 200

    logged_out = client.post(
        "/api/v1/auth/logout", headers={CSRF_HEADER: csrf_token}
    )
    assert logged_out.status_code == 204

    after = client.get("/api/v1/auth/session")
    assert after.status_code == 401
    assert _error(after)["code"] == "unauthenticated"

    # A second logout with the same (now revoked) cookie is a clean
    # 401, never a 500.
    second = client.post(
        "/api/v1/auth/logout", headers={CSRF_HEADER: csrf_token}
    )
    assert second.status_code == 401


def test_expired_session_is_unauthenticated(
    client: TestClient, postgres_db: str
) -> None:
    login = _login(client)
    assert login.status_code == 200

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

    response = client.get("/api/v1/auth/session")
    assert response.status_code == 401
    assert _error(response)["code"] == "unauthenticated"


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/no-such-route")
    assert response.status_code == 404
    error = _error(response)
    assert error["code"] == "not_found"


def test_error_responses_carry_request_id_without_traceback(
    client: TestClient,
) -> None:
    responses = [
        client.get("/api/v1/no-such-route"),
        client.get("/api/v1/auth/session"),
        _login(client, password="wrong-password-123"),
        _login(client, password="short-pass1"),
    ]
    for response in responses:
        error = _error(response)
        assert error["request_id"], "error body must carry a request_id"
        assert "Traceback" not in response.text
        assert "traceback" not in response.text.lower().replace(
            "traceback (most recent call last)", ""
        ) or "Traceback" not in response.text


def test_business_clock_travel_does_not_extend_session_past_db_expiry(
    postgres_db, seeded_api_operator, db_session
):
    """A27: the two clocks govern different fields and never swap.

    Session expiry is governed by the database clock only
    (app/security/sessions.py compares expires_at against
    clock_timestamp()); intake business timestamps are governed by
    the BusinessClock (app/services/intake.py stamps received_at
    from clock.now_utc()).

    Driving intake with a FakeClock travelled one year ahead must
    stamp the travelled instant on the receipt, enquiry, initial
    job and follow-up task, while the already-issued session keeps
    its DB-clock expiry and still resolves. Only moving the
    database clock past that expiry stops resolution.
    """
    from datetime import datetime, timedelta, timezone

    from app.security import sessions as session_service
    from app.services import intake
    from support.fake_clock import FakeClock
    from support.routing_config import ROUTING_CONFIG

    engine = create_engine(postgres_db)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        issued = session_service.issue_session(
            session, seeded_api_operator, "test-session-secret"
        )
        expires_before = issued.expires_at

        # Travel business time one year ahead and drive a real
        # BusinessClock consumer (intake) with the travelled clock.
        business = FakeClock(datetime.now(timezone.utc))
        business.advance_by(365 * 24 * 3600)
        travelled = business.now_utc()
        assert travelled > expires_before
        intake.submit_intake(
            db_session,
            {
                "first_name": "Alex",
                "last_name": "Morgan",
                "email": "alex.demo@example.com",
                "phone": "+61412345678",
                "postcode": "2150",
                "service_type": "bathroom",
                "message": "I need a bathroom renovation next month.",
                "preferred_contact": "email",
                "acknowledge_demo": True,
            },
            source="web",
            event_key=uuid.uuid4(),
            clock=business,
            routing_config=ROUTING_CONFIG,
        )

        # Read back with raw SQL, never through the service under test.
        read_engine = create_engine(postgres_db)
        try:
            with read_engine.connect() as conn:
                receipt_at = conn.execute(
                    text("SELECT received_at FROM leadops.ingestion_receipts")
                ).scalar_one()
                enquiry_at = conn.execute(
                    text("SELECT received_at FROM leadops.enquiries")
                ).scalar_one()
                job_at = conn.execute(
                    text(
                        "SELECT next_attempt_at FROM leadops.jobs "
                        "WHERE kind = 'prepare_enquiry'"
                    )
                ).scalar_one()
                task_due_at = conn.execute(
                    text("SELECT due_at FROM leadops.follow_up_tasks")
                ).scalar_one()
        finally:
            read_engine.dispose()

        # Business timestamps follow the travelled BusinessClock.
        assert receipt_at == travelled
        assert enquiry_at == travelled
        assert job_at == travelled
        assert task_due_at == travelled + timedelta(days=1)

        # The session is untouched by business travel: same DB-clock
        # expiry, and it still resolves even though business time is
        # now a year past that expiry.
        resolved = session_service.resolve_session(
            session, issued.token, "test-session-secret"
        )
        assert resolved is not None
        assert resolved.expires_at == expires_before

        # Move only the database clock past expiry; business clock
        # stays put, yet the session no longer resolves.
        session.execute(
            text(
                "UPDATE leadops.sessions SET expires_at = "
                "clock_timestamp() - interval '1 minute'"
            )
        )
        session.commit()
        assert (
            session_service.resolve_session(
                session, issued.token, "test-session-secret"
            )
            is None
        )
    finally:
        session.close()
        engine.dispose()
