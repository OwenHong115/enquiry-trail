"""R7a red test: a non-ASCII CSRF header must be a 403, not a 500.

hmac.compare_digest raises TypeError on non-ASCII str operands,
so a crafted X-CSRF-Token currently escapes require_operator as
an unhandled 500 instead of the contracted 403 csrf_invalid.

The non-ASCII value is sent as raw latin-1 bytes (b"\\xe9"):
httpx rejects a non-ASCII str header, and Starlette decodes the
received bytes as latin-1, which is exactly the non-ASCII str
that reaches compare_digest.

Operators are reference data and are not truncated between
tests, so the deactivation in the precedence guard is restored
in a finally block.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

CSRF_HEADER = "X-CSRF-Token"
NON_ASCII_CSRF = b"\xe9"


def _error(response) -> dict:
    body = response.json()
    assert "error" in body, f"not an Error envelope: {body!r}"
    return body["error"]


def _resolve(api_client: TestClient, enquiry_id: uuid.UUID, headers: dict):
    return api_client.post(
        f"/api/v1/enquiries/{enquiry_id}/review/resolve",
        json={
            "expected_version": 1,
            "resolution_note": "Confirmed scope by phone",
        },
        headers=headers,
    )


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


def test_non_ascii_csrf_header_is_403_not_500(
    api_client: TestClient,
) -> None:
    enquiry_id = uuid.uuid4()

    # Guard 1 (passes today): no CSRF header is a clean 403.
    missing = _resolve(api_client, enquiry_id, headers={})
    assert missing.status_code == 403, missing.text
    assert _error(missing)["code"] == "csrf_invalid"

    # Guard 2 (passes today): the correct token passes the CSRF
    # check, so a non-existent enquiry proves it with a 404.
    correct = _resolve(
        api_client,
        enquiry_id,
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )
    assert correct.status_code == 404, correct.text
    assert _error(correct)["code"] == "not_found"

    # The pinned defect: a non-ASCII token must be rejected as
    # csrf_invalid, never escape compare_digest as a 500.
    response = _resolve(
        api_client, enquiry_id, headers={CSRF_HEADER: NON_ASCII_CSRF}
    )
    assert response.status_code == 403, response.text
    assert _error(response)["code"] == "csrf_invalid"


def test_deactivated_session_outranks_non_ascii_csrf(
    api_client: TestClient, api_operator: object, postgres_db: str
) -> None:
    """Precedence guard (passes today, pins R4): 401 before 403.

    Session resolution runs before the CSRF check, so even a
    malformed CSRF header on a deactivated operator's session is
    an unauthenticated 401, not a 403 and not a 500.
    """
    try:
        _set_active(postgres_db, api_operator, False)
        response = _resolve(
            api_client,
            uuid.uuid4(),
            headers={CSRF_HEADER: NON_ASCII_CSRF},
        )
        assert response.status_code == 401, response.text
        assert _error(response)["code"] == "unauthenticated"
    finally:
        # Operators are not truncated between tests: always restore.
        _set_active(postgres_db, api_operator, True)
