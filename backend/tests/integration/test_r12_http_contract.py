"""R12 red tests: the 500 correlation path and the OpenAPI contract.

Defect A: app/bootstrap.py registers the request-id middleware
and the generic Exception handler. In Starlette the handler for
Exception is installed on ServerErrorMiddleware, which sits
OUTSIDE user middleware, so an escaping exception never reaches
the middleware's post-call_next lines: no X-Request-ID header is
set and no record is emitted. The handler itself only builds an
envelope. Verified shape of the gap: an unhandled exception
yields a 500 with no X-Request-ID header and zero structured log
records.

Defect B: the OpenAPI schema does not describe the enforced auth
or error contract. There are no components.securitySchemes at
all, and the write operations declare only 200 and 422, so a
client generated from the schema cannot see that the endpoints
need a session cookie, need CSRF, or can answer 401/403/404/409.
(X-CSRF-Token already appears as a parameter today; that part of
the surface is pinned as a passing guard below.)
"""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient

RESOLVE_PATH = "/api/v1/enquiries/{enquiry_id}/review/resolve"
DETAIL_PATH = "/api/v1/enquiries/{enquiry_id}"
ERROR_REF = "#/components/schemas/ErrorEnvelope"


def _parsed_records(caplog: pytest.LogCaptureFixture) -> list[dict]:
    parsed: list[dict] = []
    for record in caplog.records:
        try:
            value = json.loads(record.getMessage())
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            parsed.append(value)
    return parsed


def test_unhandled_exception_keeps_request_id_and_is_logged(
    api_client: TestClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.api.enquiries.build_enquiry_detail", boom
    )

    with caplog.at_level(logging.INFO):
        response = api_client.get(f"/api/v1/enquiries/{uuid.uuid4()}")

    assert response.status_code == 500, response.text

    # The correlation id must survive the 500: present as a
    # header, and the same id the envelope carries.
    request_id = response.headers.get("X-Request-ID")
    assert request_id, "500 response carries no X-Request-ID header"
    assert response.json()["error"]["request_id"] == request_id

    parsed = _parsed_records(caplog)
    requests = [
        record
        for record in parsed
        if record.get("event") == "http.request"
        and record.get("request_id") == request_id
        and record.get("status") == 500
    ]
    assert len(requests) == 1, parsed

    errors = [
        record
        for record in parsed
        if record.get("event") == "http.error"
        and record.get("request_id") == request_id
    ]
    assert len(errors) == 1, parsed
    error = errors[0]
    # Identifier only: the exception class name, never its
    # message, payload or any contact data.
    assert error.get("error_code") == "RuntimeError"
    assert set(error) <= {"event", "request_id", "error_code"}
    assert "boom" not in json.dumps(error)


def test_openapi_declares_session_csrf_and_error_responses(
    api_client: TestClient,
) -> None:
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    response = anonymous.get("/openapi.json")
    assert response.status_code == 200, response.text
    schema = response.json()

    paths = schema["paths"]
    resolve = paths[RESOLVE_PATH]["post"]
    detail = paths[DETAIL_PATH]["get"]

    # Passing guard (true today): CSRF is already visible as a
    # parameter on the write operation.
    parameter_names = {
        parameter["name"] for parameter in resolve.get("parameters", [])
    }
    assert "X-CSRF-Token" in parameter_names

    # Exactly one scheme: the session cookie.
    schemes = schema["components"].get("securitySchemes", {})
    assert set(schemes) == {"sessionCookie"}
    scheme = schemes["sessionCookie"]
    assert scheme["type"] == "apiKey"
    assert scheme["in"] == "cookie"
    assert scheme["name"] == "leadops_session"

    # Both operations require the session, and declare the error
    # responses they can actually produce.
    assert resolve.get("security") == [{"sessionCookie": []}]
    assert detail.get("security") == [{"sessionCookie": []}]
    resolve_responses = resolve["responses"]
    detail_responses = detail["responses"]
    for status in ("200", "401", "403", "409", "422"):
        assert status in resolve_responses, sorted(resolve_responses)
    for status in ("200", "401", "404"):
        assert status in detail_responses, sorted(detail_responses)

    # Every declared 4xx answers with the ErrorEnvelope schema,
    # which must exist in components.schemas.
    assert "ErrorEnvelope" in schema["components"]["schemas"]
    for operation, statuses in (
        (resolve_responses, ("401", "403", "409", "422")),
        (detail_responses, ("401", "404")),
    ):
        for status in statuses:
            content = operation[status]["content"]["application/json"]
            assert content["schema"]["$ref"] == ERROR_REF, (
                status,
                content,
            )
