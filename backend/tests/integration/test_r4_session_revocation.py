"""R4 red test: session revocation on operator deactivation.

resolve_session joins operators but never checks operators.active,
so a session issued before deactivation keeps working until it
expires or is revoked. Login is already safe (get_active_operator
filters active), so test 2 is a passing regression guard and is
declared as such.

Operators are reference data and are NOT truncated between tests,
so every deactivation here is restored in a finally block;
otherwise the deactivated row would poison the whole session.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

# Seeded by conftest's api_operator fixture.
USERNAME = "intake-operator"
PASSWORD = "intake-demo-pass"
CSRF_HEADER = "X-CSRF-Token"


def _error(response) -> dict:
    body = response.json()
    assert "error" in body, f"not an Error envelope: {body!r}"
    return body["error"]


def _set_active(postgres_db: str, operator_id: object, active: bool) -> None:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE leadops.operators SET active = :active "
                    "WHERE id = :operator_id"
                ),
                {"active": active, "operator_id": operator_id},
            )
    finally:
        engine.dispose()


def test_deactivated_operators_live_session_is_rejected(
    api_client: TestClient, api_operator: object, postgres_db: str
) -> None:
    before = api_client.get("/api/v1/auth/session")
    assert before.status_code == 200, before.text

    try:
        _set_active(postgres_db, api_operator, False)

        # The same cookie must stop working immediately, not at
        # expiry: resolve_session has to check operators.active.
        session = api_client.get("/api/v1/auth/session")
        assert session.status_code == 401, session.text
        assert _error(session)["code"] == "unauthenticated"

        # A write with a valid CSRF token must also get 401, not
        # 403: the session is resolved (and rejected) before CSRF
        # is ever checked. A random enquiry id is enough — auth
        # runs before any business lookup.
        write = api_client.post(
            f"/api/v1/enquiries/{uuid.uuid4()}/review/resolve",
            json={"expected_version": 1, "resolution_note": "Confirmed"},
            headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
        )
        assert write.status_code == 401, write.text
        assert _error(write)["code"] == "unauthenticated"
    finally:
        # Operators are not truncated between tests: always restore.
        _set_active(postgres_db, api_operator, True)

    # Reactivation restores access for the still-live session.
    after = api_client.get("/api/v1/auth/session")
    assert after.status_code == 200, after.text


def test_deactivated_operator_cannot_log_in(
    api_client: TestClient, api_operator: object, postgres_db: str
) -> None:
    """Expected to PASS today: login already filters active.

    This is a regression guard for get_active_operator, declared
    passing so the slice's one red test is test 1 above.
    """
    try:
        _set_active(postgres_db, api_operator, False)

        login = api_client.post(
            "/api/v1/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
        )
        assert login.status_code == 401
        assert _error(login)["code"] == "invalid_credentials"

        # Deactivation is indistinguishable from a bad credential:
        # same status, code and message as an unknown username.
        unknown = api_client.post(
            "/api/v1/auth/login",
            json={"username": "no-such-user", "password": PASSWORD},
        )
        assert unknown.status_code == 401
        assert _error(unknown)["code"] == _error(login)["code"]
        assert _error(unknown)["message"] == _error(login)["message"]
    finally:
        _set_active(postgres_db, api_operator, True)
