"""R13 red tests: atomic provider blocking, bounded statements.

Defect A: app/services/health.py block_provider is
check-then-act. It reads the health row, returns early when
already blocked, otherwise inserts or updates the row and emits
integration.blocked. Two workers that hit auth_failed at the
same time can both observe "not blocked": the loser either
raises a unique violation on the integration_health primary key
(rolling back its own job-blocking write) or opens a second
incident and a second event. The docstring promises "an
already-blocked provider keeps its incident, its blocked_at and
its single blocked event".

Defect B: app/db/session.py make_engine sets
connect_args connect_timeout=2, which bounds only connection
establishment. On the live database SHOW statement_timeout
returns 0, so a query that stalls after connecting can hold
/ready open indefinitely. Test 2 pins the configured bound on
the application's own engine rather than simulating a stall:
SELECT 1 cannot be blocked by locks, and a genuine stall is not
reproducible without a hung server, so asserting the engine's
configured statement_timeout is the only honest local proof
that the bound exists. Green must set it (e.g. via server
options) on the same engine factory the readiness probe uses.

Threads and a barrier only; no sleeps.
"""

from __future__ import annotations

import re
import threading
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.health import block_provider

CSRF_HEADER = "X-CSRF-Token"
KEY_HEADER = "Idempotency-Key"

ALEX: dict = {
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": "blocking@example.com",
    "phone": "+61412345678",
    "postcode": "2150",
    "service_type": "bathroom",
    "message": "I need a bathroom renovation next month, around AUD 20,000.",
    "preferred_contact": "email",
    "acknowledge_demo": True,
}


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _submit(api_client: TestClient) -> uuid.UUID:
    response = api_client.post(
        "/api/v1/intake/form",
        json=ALEX,
        headers={
            KEY_HEADER: str(uuid.uuid4()),
            CSRF_HEADER: api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["enquiry_id"])


def test_concurrent_auth_failures_open_exactly_one_incident(
    api_client: TestClient, postgres_db: str
) -> None:
    enquiry_id = _submit(api_client)

    engine = create_engine(postgres_db)
    factory = sessionmaker(bind=engine)
    barrier = threading.Barrier(8)
    incident_ids: list[uuid.UUID] = []
    errors: list[BaseException] = []

    def worker() -> None:
        session = factory()
        try:
            barrier.wait(timeout=15)
            # Callers run block_provider inside their own write
            # transaction (see the auth_failed blocks in
            # app/worker/claiming.py); the threads do the same.
            with session.begin():
                incident_ids.append(
                    block_provider(session, enquiry_id=enquiry_id)
                )
        except BaseException as exc:  # collected and asserted below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    engine.dispose()

    assert not any(thread.is_alive() for thread in threads)
    # The red: today at least one contender raises a unique
    # violation on the integration_health primary key.
    assert errors == [], f"unexpected block_provider errors: {errors!r}"
    assert len(incident_ids) == 8

    rows = _fetch(
        postgres_db,
        "SELECT provider, status, incident_id "
        "FROM leadops.integration_health WHERE provider = 'hubspot'",
    )
    assert len(rows) == 1
    assert rows[0].status == "blocked"

    events = _fetch(
        postgres_db,
        "SELECT id FROM leadops.activity_events "
        "WHERE event_type = 'integration.blocked'",
    )
    assert len(events) == 1

    # Every contender was handed the one live incident.
    assert set(incident_ids) == {rows[0].incident_id}


def _statement_timeout_ms(raw: str) -> float:
    """Parse a SHOW statement_timeout value into milliseconds."""
    value = raw.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|min|h)?", value)
    assert match is not None, f"unparseable statement_timeout: {raw!r}"
    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "s":
        return amount * 1000
    if unit == "min":
        return amount * 60_000
    if unit == "h":
        return amount * 3_600_000
    # "ms" or a bare number (PostgreSQL reports bare values in ms).
    return amount


def test_application_engine_bounds_statement_time(
    api_client: TestClient,
) -> None:
    engine = api_client.app.state.engine
    with engine.connect() as conn:
        raw = conn.execute(text("SHOW statement_timeout")).scalar_one()

    # The red: today the value is "0" (no bound at all), so a
    # statement that stalls after connecting is unbounded even
    # though connection establishment is capped at 2s.
    assert raw != "0", "statement_timeout is unset on the app engine"
    parsed_ms = _statement_timeout_ms(raw)
    assert 0 < parsed_ms <= 5000
