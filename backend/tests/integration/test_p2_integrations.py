"""P2: integration status/resume and worker health.

Seeded integration_health rows, a versioned operator resume that
probes through app.state.crm, and DB-clock worker staleness.
"""

from __future__ import annotations

import threading
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.seed import seed
from support.fake_crm import FakeCrm

REASON = "Provider confirmed healthy again"


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _exec(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return result.all() if result.returns_rows else []
    finally:
        engine.dispose()


def _seed(postgres_db: str) -> None:
    session = _factory(postgres_db)()
    try:
        seed(session)
    finally:
        session.close()


def _integrations(api_client: TestClient) -> list[dict]:
    response = api_client.get("/api/v1/integrations")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _hubspot(api_client: TestClient) -> dict:
    return next(i for i in _integrations(api_client) if i["provider"] == "hubspot")


def _block_provider(postgres_db: str, api_client: TestClient) -> dict:
    _seed(postgres_db)
    _exec(
        postgres_db,
        "UPDATE leadops.integration_health SET status='blocked', "
        "incident_id=:inc, blocked_at=now(), last_error_code='auth_failed', "
        "updated_at=now() WHERE provider='hubspot'",
        {"inc": str(uuid.uuid4())},
    )
    # A blocked sync job belonging to a submitted enquiry.
    submit = api_client.post(
        "/api/v1/intake/form",
        json={
            "first_name": "Alex", "last_name": "Morgan",
            "email": "blocked@example.com", "phone": "+61412345678",
            "postcode": "2150", "service_type": "bathroom",
            "message": "Blocked provider enquiry with a long message.",
            "preferred_contact": "email", "acknowledge_demo": True,
        },
        headers={
            "Idempotency-Key": str(uuid.uuid4()),
            "X-CSRF-Token": api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert submit.status_code == 202, submit.text
    enquiry_id = submit.json()["enquiry_id"]
    _exec(
        postgres_db,
        "INSERT INTO leadops.jobs (kind, entity_id, dedupe_key, state, "
        "next_attempt_at) SELECT 'sync_contact', e.id, "
        "'sync_contact:' || e.contact_id::text, 'blocked', now() "
        "FROM leadops.enquiries e WHERE e.id=:id",
        {"id": enquiry_id},
    )
    return _hubspot(api_client)


def _resume(api_client: TestClient, provider: str, body: dict,
            csrf: str | None = "valid"):
    headers = {}
    if csrf == "valid":
        headers["X-CSRF-Token"] = api_client.csrf_token  # type: ignore[attr-defined]
    return api_client.post(
        f"/api/v1/integrations/{provider}/resume", json=body, headers=headers
    )


def _valid_body(item: dict, **overrides) -> dict:
    body = {
        "expected_status": "blocked",
        "expected_updated_at": item["updated_at"],
        "reason": REASON,
    }
    body.update(overrides)
    return body


def test_seed_integration_rows_idempotent_and_block_preserved(
    postgres_db: str,
) -> None:
    _seed(postgres_db)
    _seed(postgres_db)
    rows = _exec(
        postgres_db,
        "SELECT provider, status FROM leadops.integration_health "
        "ORDER BY provider",
    )
    assert [(r.provider, r.status) for r in rows] == [
        ("ai", "disabled"), ("hubspot", "ok"), ("slack", "disabled"),
    ]
    _exec(
        postgres_db,
        "UPDATE leadops.integration_health SET status='blocked' "
        "WHERE provider='hubspot'",
    )
    _seed(postgres_db)
    rows = _exec(
        postgres_db,
        "SELECT status FROM leadops.integration_health WHERE provider='hubspot'",
    )
    assert rows[0].status == "blocked"


def test_integrations_list_auth_and_shape(
    api_client: TestClient, postgres_db: str
) -> None:
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    assert anonymous.get("/api/v1/integrations").status_code == 401
    _seed(postgres_db)
    items = _integrations(api_client)
    assert [i["provider"] for i in items] == ["ai", "hubspot", "slack"]
    assert {i["provider"]: i["status"] for i in items} == {
        "ai": "disabled", "hubspot": "ok", "slack": "disabled",
    }


def test_integrations_blocked_row_fields(
    api_client: TestClient, postgres_db: str
) -> None:
    item = _block_provider(postgres_db, api_client)
    assert item["status"] == "blocked"
    assert item["incident_id"] is not None
    assert item["last_error_code"] == "auth_failed"
    assert item["blocked_at"] is not None
    assert item["updated_at"] is not None


def test_resume_success_requeues_and_emits_once(
    api_client: TestClient, postgres_db: str
) -> None:
    item = _block_provider(postgres_db, api_client)
    api_client.app.state.crm = FakeCrm(probe_ok=True)
    response = _resume(api_client, "hubspot", _valid_body(item))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "hubspot"
    assert body["status"] == "ok"
    assert body["blocked_at"] is None
    assert body["last_error_code"] is None
    assert body["last_success_at"] is not None
    jobs = _exec(
        postgres_db, "SELECT state FROM leadops.jobs WHERE kind='sync_contact'"
    )
    assert [j.state for j in jobs] == ["pending"]
    events = _exec(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.activity_events "
        "WHERE event_type='integration.resumed'",
    )
    assert events[0].n == 1


def test_resume_failed_probe_changes_nothing(
    api_client: TestClient, postgres_db: str
) -> None:
    item = _block_provider(postgres_db, api_client)
    api_client.app.state.crm = FakeCrm(probe_ok=False)
    response = _resume(api_client, "hubspot", _valid_body(item))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "resume_failed"
    assert _hubspot(api_client)["status"] == "blocked"
    jobs = _exec(
        postgres_db, "SELECT state FROM leadops.jobs WHERE kind='sync_contact'"
    )
    assert [j.state for j in jobs] == ["blocked"]
    events = _exec(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.activity_events "
        "WHERE event_type='integration.resumed'",
    )
    assert events[0].n == 0


def test_resume_conflicts_and_validation(
    api_client: TestClient, postgres_db: str
) -> None:
    _seed(postgres_db)
    ok_item = _hubspot(api_client)
    api_client.app.state.crm = FakeCrm(probe_ok=True)
    # Not blocked.
    response = _resume(api_client, "hubspot", _valid_body(ok_item))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state"

    blocked = _block_provider(postgres_db, api_client)
    # Stale expected_updated_at.
    response = _resume(
        api_client, "hubspot",
        _valid_body(blocked, expected_updated_at="2020-01-01T00:00:00Z"),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "version_conflict"
    # Unknown provider.
    response = _resume(api_client, "nope", _valid_body(blocked))
    assert response.status_code == 404
    # Missing CSRF.
    response = _resume(api_client, "hubspot", _valid_body(blocked), csrf=None)
    assert response.status_code == 403
    # Invalid bodies.
    assert _resume(
        api_client, "hubspot", _valid_body(blocked, reason="short")
    ).status_code == 422
    assert _resume(
        api_client, "hubspot", _valid_body(blocked, expected_status="ok")
    ).status_code == 422
    assert _resume(
        api_client, "hubspot", _valid_body(blocked, surprise=1)
    ).status_code == 422


def _authed_client(app) -> TestClient:
    client = TestClient(app, raise_server_exceptions=False)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "intake-operator", "password": "intake-demo-pass"},
    )
    assert login.status_code == 200, login.text
    client.csrf_token = login.json()["csrf_token"]  # type: ignore[attr-defined]
    return client


def test_resume_concurrent_one_success(
    api_client: TestClient, postgres_db: str
) -> None:
    item = _block_provider(postgres_db, api_client)
    api_client.app.state.crm = FakeCrm(probe_ok=True)
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def attempt() -> None:
        client = _authed_client(api_client.app)
        barrier.wait(timeout=15)
        response = _resume(client, "hubspot", _valid_body(item))
        statuses.append(response.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(statuses) == [200, 409]
    events = _exec(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.activity_events "
        "WHERE event_type='integration.resumed'",
    )
    assert events[0].n == 1
    jobs = _exec(
        postgres_db, "SELECT state, cycle_no FROM leadops.jobs "
        "WHERE kind='sync_contact'",
    )
    assert [(j.state, j.cycle_no) for j in jobs] == [("pending", 2)]


def test_workers_health(api_client: TestClient, postgres_db: str) -> None:
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    assert anonymous.get("/api/v1/health/workers").status_code == 401

    body = api_client.get("/api/v1/health/workers").json()
    assert body["workers"] == []
    assert body["stale_after_seconds"] >= 15

    alive_id, stale_id = str(uuid.uuid4()), str(uuid.uuid4())
    _exec(
        postgres_db,
        "INSERT INTO leadops.worker_heartbeats (worker_id, started_at, "
        "last_seen_at, active_job_count) VALUES (:id, now(), now(), 2)",
        {"id": alive_id},
    )
    _exec(
        postgres_db,
        "INSERT INTO leadops.worker_heartbeats (worker_id, started_at, "
        "last_seen_at, active_job_count) VALUES (:id, "
        "now() - interval '1 hour', now() - interval '10 minutes', 0)",
        {"id": stale_id},
    )
    body = api_client.get("/api/v1/health/workers").json()
    by_id = {w["worker_id"]: w for w in body["workers"]}
    assert by_id[alive_id]["status"] == "alive"
    assert by_id[alive_id]["active_job_count"] == 2
    assert by_id[stale_id]["status"] == "stale"
