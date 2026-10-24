"""R7c red tests: structured, correlated logging.

Defect: the application emits no logs at all (there is no
``import logging`` anywhere under app/). A failed job, a blocked
provider or a 500 leaves nothing but the HTTP response, so an
incident cannot be reconstructed. The X-Request-ID middleware in
app/bootstrap.py already produces a correlation id per request,
but nothing writes it anywhere.

Contract pinned for green:

- Every record an application logger emits has a message that is a
  JSON object string: json.loads(record.getMessage()) is a dict
  containing an "event" key.
- HTTP request records: event "http.request", with request_id,
  method, path and status. request_id equals the X-Request-ID
  response header for that request.
- Job records: event "job.handled" when a claimed job is finished,
  with job_id, kind, attempt_no and outcome.
- Records propagate normally so caplog sees them; no propagation
  disabling and no handlers installed at import time.
- Logs never contain secrets or personal data: no session cookie
  value, no CSRF token, no raw email or phone.

Worker correlation gap (documented, out of scope): the HTTP
request id is not propagated into the worker. That would need a
persisted column, a schema change and a new migration, so
correlating API intake to worker handling is deferred to S9.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.intake import submit_intake
from app.worker.runner import run_once
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
COOKIE_NAME = "leadops_session"
SESSION_SECRET = "test-session-secret"
# conftest.API_OPERATOR_PASSWORD for the api_client operator.
OPERATOR_PASSWORD = "intake-demo-pass"


def _payload() -> dict:
    return {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": "logging@example.com",
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": "Renovation enquiry with enough detail for intake.",
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _parsed_records(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """Every captured message that parses as a JSON object."""
    parsed: list[dict] = []
    for record in caplog.records:
        try:
            value = json.loads(record.getMessage())
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            parsed.append(value)
    return parsed


def test_handled_job_emits_one_structured_record(
    postgres_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock(T0)
    session = _factory(postgres_db)()
    try:
        result = submit_intake(
            session,
            _payload(),
            source=SOURCE,
            event_key=uuid.uuid4(),
            clock=clock,
            routing_config=ROUTING_CONFIG,
        )
    finally:
        session.close()

    job_id = _fetch(
        postgres_db,
        "SELECT id FROM leadops.jobs WHERE entity_id = :enquiry_id "
        "AND kind = 'prepare_enquiry'",
        {"enquiry_id": result.enquiry_id},
    )[0].id

    session = _factory(postgres_db)()
    try:
        with caplog.at_level(logging.INFO):
            handled = run_once(session, WORKER, clock, FakeCrm())
    finally:
        session.close()

    # Behaviour guard (already passes today): the job really ran.
    assert handled is True
    state = _fetch(
        postgres_db,
        "SELECT state FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0].state
    assert state == "succeeded"

    parsed = _parsed_records(caplog)
    # No partially structured lines from app loggers.
    assert all("event" in record for record in parsed), parsed

    matching = [
        record
        for record in parsed
        if record.get("event") == "job.handled"
        and record.get("job_id") == str(job_id)
        and record.get("kind") == "prepare_enquiry"
        and record.get("attempt_no") == 1
        and record.get("outcome") == "succeeded"
    ]
    assert matching, (
        "no matching structured record with event 'job.handled' for "
        f"job {job_id}: parsed records were {parsed!r}"
    )


def test_request_record_carries_correlation_id_and_nothing_leaks(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        response = api_client.get("/api/v1/auth/session")

    # Behaviour guard (already passes today): the request succeeds
    # and the middleware already mints the correlation id.
    assert response.status_code == 200, response.text
    request_id = response.headers["X-Request-ID"]
    assert request_id

    # Leak guards. These already pass today because nothing is
    # logged yet; the point is that green must not introduce a
    # leak. Each guarded value is asserted non-empty first so the
    # guard cannot pass vacuously on an empty string.
    cookie_value = api_client.cookies.get(COOKIE_NAME)
    csrf_token = api_client.csrf_token  # type: ignore[attr-defined]
    assert cookie_value
    assert csrf_token
    assert SESSION_SECRET
    assert OPERATOR_PASSWORD
    all_messages = "\n".join(
        record.getMessage() for record in caplog.records
    )
    assert cookie_value not in all_messages
    assert csrf_token not in all_messages
    assert SESSION_SECRET not in all_messages
    assert OPERATOR_PASSWORD not in all_messages

    parsed = _parsed_records(caplog)
    assert all("event" in record for record in parsed), parsed

    matching = [
        record
        for record in parsed
        if record.get("event") == "http.request"
        and record.get("request_id") == request_id
        and record.get("method") == "GET"
        and record.get("path") == "/api/v1/auth/session"
        and record.get("status") == 200
    ]
    assert matching, (
        "no matching structured record with event 'http.request' "
        f"and request_id {request_id}: parsed records were {parsed!r}"
    )
