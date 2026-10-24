"""R7b red tests: readiness must prove the database, not just the process.

Defect: app/api/health.py exposes only GET /api/v1/health/live, which
returns {"status": "ok"} without touching the database. There is no
readiness probe, so a process with a dead database still reports
healthy and receives traffic.

Pin: GET /api/v1/health/ready returns 200 {"status": "ok"} when the
database is reachable, and 503 (never an exception, and promptly, so
the check is bounded) when it is not. Liveness stays distinct and
keeps returning 200 in both cases. Health routes require no session,
so both probes are exercised anonymously.
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.bootstrap import create_app
from app.settings import Settings

DEAD_DATABASE_URL = (
    "postgresql+psycopg://leadops:leadops@127.0.0.1:1/leadops"
)


def test_ready_ok_with_real_database(api_client: TestClient) -> None:
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)

    live = anonymous.get("/api/v1/health/live")
    assert live.status_code == 200, live.text
    assert live.json()["status"] == "ok"

    ready = anonymous.get("/api/v1/health/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ok"


def test_ready_fails_closed_with_dead_database() -> None:
    app = create_app(
        Settings(
            database_url=DEAD_DATABASE_URL,
            session_secret="test-session-secret",
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    live = client.get("/api/v1/health/live")
    assert live.status_code == 200, live.text
    assert live.json()["status"] == "ok"

    started = time.monotonic()
    ready = client.get("/api/v1/health/ready")
    elapsed = time.monotonic() - started

    # Fails closed: a dead database is a prompt 503, never a raised
    # exception and never a wait for a full default connect timeout.
    assert ready.status_code == 503, ready.text
    assert elapsed < 5, f"readiness took {elapsed:.2f}s"
