"""R17 red tests: entrypoint logging and the readiness pool bound.

Defect A: the application configures no logging.
app/logging.py deliberately installs no handler and sets no
level, so in the running API and worker processes the "leadops"
logger inherits the root WARNING level and every log_event INFO
record is discarded. The R7c tests pass only because caplog
raises the level; that proves the calls exist, not that a
deployed process emits anything. Test 1 therefore does NOT use
caplog - caplog masks exactly the defect this test exists to
catch. It captures process-level output with capfd instead.

Defect B: the readiness probe has no bound on pool wait.
make_engine sets connect_timeout and statement_timeout (R13)
but nothing bounds waiting for a connection from the SQLAlchemy
pool. When the pool is exhausted, /ready can block far past
the 5s bound (today: until the pool's default checkout timeout,
tens of seconds). Test 2 saturates the app engine's own pool
and measures the call.
"""

from __future__ import annotations

import json
import logging
import time

import pytest
from fastapi.testclient import TestClient


def test_configured_process_emits_a_structured_record(
    api_client: TestClient, capfd: pytest.CaptureFixture[str]
) -> None:
    response = api_client.get("/api/v1/health/live")
    assert response.status_code == 200, response.text
    request_id = response.headers["X-Request-ID"]
    assert request_id

    captured = capfd.readouterr()
    lines = (captured.out + captured.err).splitlines()
    parsed: list[dict] = []
    for line in lines:
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            parsed.append(value)

    matching = [
        record
        for record in parsed
        if record.get("event") == "http.request"
        and record.get("request_id") == request_id
    ]
    assert matching, (
        "no structured http.request line reached the process "
        f"output for request {request_id}: captured lines "
        f"were {lines!r}"
    )
    # The logger itself must be enabled for INFO in a configured
    # process, not only under caplog.
    assert logging.getLogger("leadops").isEnabledFor(logging.INFO)


def test_ready_answers_503_within_bounds_when_pool_exhausted(
    api_client: TestClient,
) -> None:
    engine = api_client.app.state.engine
    pool = engine.pool
    capacity = pool.size() + pool._max_overflow  # type: ignore[attr-defined]
    held = []
    try:
        for _ in range(capacity):
            held.append(engine.connect())

        started = time.monotonic()
        response = api_client.get("/api/v1/health/ready")
        elapsed = time.monotonic() - started
    finally:
        for connection in held:
            connection.close()

    assert response.status_code == 503, response.text
    assert elapsed < 5, (
        f"/ready took {elapsed:.1f}s with the pool exhausted; "
        "the pool wait is unbounded"
    )
