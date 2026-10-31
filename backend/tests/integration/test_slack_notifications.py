"""Slack notification integration tests (A19/A20, disabled default).

Behavioural evidence only: persisted rows, events, job states and
simulator message counts. A19: one ready event makes exactly one
notification, one provider message and one notification.sent event.
A20: a lost success response leaves delivery_unknown with exactly
one provider message and no automatic resend; a definite failure
records failed. Disabled mode enqueues nothing.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.adapters.notifications_sim import HttpNotificationAdapter
from app.ports.notifications import NotificationError
from app.services import notifications as notification_service
from app.worker.runner import run_until_idle
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_notifications import FakeNotifications
from support.routing_config import ROUTING_CONFIG

T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)
WORKER = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
ROOT = Path(__file__).resolve().parents[3]
TOKEN = "test-sim-token"


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": "alex.demo@example.com",
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": "I need a bathroom renovation next month.",
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }
    payload.update(overrides)
    return payload


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _submit(postgres_db: str, clock: FakeClock) -> uuid.UUID:
    from app.services.intake import submit_intake

    session = _factory(postgres_db)()
    try:
        result = submit_intake(
            session, _payload(), source="web", event_key=uuid.uuid4(),
            clock=clock, routing_config=ROUTING_CONFIG,
        )
        return result.enquiry_id
    finally:
        session.close()


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _drain(postgres_db, clock, crm, notifications=None) -> int:
    return run_until_idle(
        _factory(postgres_db), WORKER, clock, crm,
        heartbeat_interval_seconds=0, notifications=notifications,
    )


def _ready_enquiry(postgres_db, clock, notifications=None):
    enquiry_id = _submit(postgres_db, clock)
    crm = FakeCrm()
    _drain(postgres_db, clock, crm, notifications=notifications)
    return enquiry_id, crm


def _notifications(postgres_db):
    return _fetch(
        postgres_db,
        "SELECT id, enquiry_id, kind, status, operation_key, last_error_code "
        "FROM leadops.notifications ORDER BY id",
    )


def _events(postgres_db, event_type: str):
    return _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events WHERE event_type = :t",
        {"t": event_type},
    )


def _jobs(postgres_db, kind: str):
    return _fetch(
        postgres_db,
        "SELECT id, state, last_error_code FROM leadops.jobs WHERE kind = :k ORDER BY created_at",
        {"k": kind},
    )


def test_disabled_mode_no_notification_no_job(postgres_db: str) -> None:
    clock = FakeClock(T0)
    enquiry_id, _crm = _ready_enquiry(postgres_db, clock, notifications=None)
    assert _notifications(postgres_db) == []
    assert _jobs(postgres_db, "notify_ready") == []
    # The enquiry itself still reaches ready/synced exactly as today.
    links = _fetch(postgres_db, "SELECT sync_status FROM leadops.crm_links")
    assert links[0].sync_status == "synced"


def test_a19_one_ready_one_notification_one_message_one_event(postgres_db: str) -> None:
    clock = FakeClock(T0)
    fake = FakeNotifications()
    enquiry_id, _crm = _ready_enquiry(postgres_db, clock, notifications=fake)

    rows = _notifications(postgres_db)
    assert len(rows) == 1
    assert rows[0].enquiry_id == enquiry_id
    assert rows[0].status == "sent"
    assert fake.sent_count == 1
    assert len(_events(postgres_db, "notification.sent")) == 1
    jobs = _jobs(postgres_db, "notify_ready")
    assert len(jobs) == 1 and jobs[0].state == "succeeded"

    # Re-delivery / re-drain of the same ready event: still exactly one.
    session = _factory(postgres_db)()
    try:
        with session.begin():
            notification_service.enqueue_ready_notification(
                session, enquiry_id, enabled=True, now=clock.now_utc()
            )
    finally:
        session.close()
    _drain(postgres_db, clock, FakeCrm(), notifications=fake)
    assert len(_notifications(postgres_db)) == 1
    assert fake.sent_count == 1
    assert len(_events(postgres_db, "notification.sent")) == 1
    assert len(_jobs(postgres_db, "notify_ready")) == 1


def test_a20_lost_success_is_delivery_unknown_no_resend(postgres_db: str) -> None:
    clock = FakeClock(T0)
    fake = FakeNotifications(
        commit_then_error=NotificationError("result_unknown")
    )
    enquiry_id, _crm = _ready_enquiry(postgres_db, clock, notifications=fake)

    rows = _notifications(postgres_db)
    assert len(rows) == 1
    assert rows[0].status == "delivery_unknown"
    assert fake.sent_count == 1
    assert len(_events(postgres_db, "notification.unknown")) == 1
    # CRM state stays ready/synced and the sync job is untouched.
    assert _fetch(postgres_db, "SELECT sync_status FROM leadops.crm_links")[0].sync_status == "synced"
    assert _jobs(postgres_db, "sync_contact")[0].state == "succeeded"

    # No automatic resend on re-drain.
    _drain(postgres_db, clock, FakeCrm(), notifications=fake)
    assert fake.sent_count == 1
    assert _notifications(postgres_db)[0].status == "delivery_unknown"


def test_a20_definite_failure_records_failed(postgres_db: str) -> None:
    clock = FakeClock(T0)
    fake = FakeNotifications(error=NotificationError("validation_failed"))
    _ready_enquiry(postgres_db, clock, notifications=fake)

    rows = _notifications(postgres_db)
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].last_error_code == "validation_failed"
    assert fake.sent_count == 0
    assert len(_events(postgres_db, "notification.failed")) == 1
    assert _jobs(postgres_db, "notify_ready")[0].state == "dead"
    assert _jobs(postgres_db, "sync_contact")[0].state == "succeeded"


def test_retryable_failure_uses_job_retry_then_succeeds(postgres_db: str) -> None:
    clock = FakeClock(T0)
    fake = FakeNotifications(error=NotificationError("unavailable"))
    enquiry_id = _submit(postgres_db, clock)
    _drain(postgres_db, clock, FakeCrm(), notifications=fake)

    rows = _notifications(postgres_db)
    assert rows[0].status == "pending"
    assert rows[0].last_error_code == "unavailable"
    assert _jobs(postgres_db, "notify_ready")[0].state == "waiting_retry"
    assert _jobs(postgres_db, "sync_contact")[0].state == "succeeded"

    fake.error = None
    clock.advance_by(60)
    _drain(postgres_db, clock, FakeCrm(), notifications=fake)
    assert _notifications(postgres_db)[0].status == "sent"
    assert fake.sent_count == 1


def test_detail_reflects_notification_row(postgres_db: str, api_client) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    fake = FakeNotifications()
    enquiry_id, _crm = _ready_enquiry(postgres_db, clock, notifications=fake)

    response = api_client.get(f"/api/v1/enquiries/{enquiry_id}")
    assert response.status_code == 200, response.text
    notification = response.json()["notification"]
    row = _notifications(postgres_db)[0]
    assert notification["status"] == "sent"
    assert notification["notification_id"] == str(row.id)
    assert notification["last_error_code"] is None


def test_detail_disabled_without_notification_row(postgres_db: str, api_client) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    enquiry_id, _crm = _ready_enquiry(postgres_db, clock, notifications=None)
    response = api_client.get(f"/api/v1/enquiries/{enquiry_id}")
    assert response.status_code == 200, response.text
    assert response.json()["notification"] == {
        "status": "disabled", "notification_id": None, "last_error_code": None,
    }


# -- real-HTTP simulator evidence -----------------------------------

def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def sim():
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, SIM_TOKEN=TOKEN, PORT=str(port))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "provider-sim" / "app.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/__test__/counts", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("provider simulator did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(autouse=True)
def _reset_sim(sim):
    httpx.post(f"{sim}/__test__/reset")
    yield


def _slack_messages(sim) -> list:
    return httpx.get(f"{sim}/__test__/slack_messages").json()["messages"]


def test_http_sim_a19_sends_exactly_one_message(sim, postgres_db: str) -> None:
    clock = FakeClock(T0)
    adapter = HttpNotificationAdapter(sim, TOKEN)
    _ready_enquiry(postgres_db, clock, notifications=adapter)
    messages = _slack_messages(sim)
    assert len(messages) == 1
    assert _notifications(postgres_db)[0].status == "sent"
    assert len(_events(postgres_db, "notification.sent")) == 1


def test_http_sim_a20_drop_success_is_unknown_one_message(sim, postgres_db: str) -> None:
    httpx.post(f"{sim}/__test__/fault", json={"mode": "drop_success_response", "path": "slack"})
    clock = FakeClock(T0)
    adapter = HttpNotificationAdapter(sim, TOKEN)
    _ready_enquiry(postgres_db, clock, notifications=adapter)
    assert len(_slack_messages(sim)) == 1
    assert _notifications(postgres_db)[0].status == "delivery_unknown"
    # Re-drain never resends.
    _drain(postgres_db, clock, FakeCrm(), notifications=adapter)
    assert len(_slack_messages(sim)) == 1


def test_a19_duplicate_idempotency_key_intake_sends_exactly_once(
    sim, postgres_db: str, api_client
) -> None:
    from app.settings import Settings
    from app.worker.runner import _resolve_notifications

    clock = FakeClock(datetime.now(timezone.utc))
    key = uuid.uuid4()
    headers = {
        "Idempotency-Key": str(key),
        "X-CSRF-Token": api_client.csrf_token,  # type: ignore[attr-defined]
    }
    first = api_client.post(
        "/api/v1/intake/form", json=_payload(), headers=headers
    )
    assert first.status_code == 202, first.text
    assert first.json()["replayed"] is False
    second = api_client.post(
        "/api/v1/intake/form", json=_payload(), headers=headers
    )
    assert second.status_code == 202, second.text
    assert second.json()["replayed"] is True
    assert second.json()["enquiry_id"] == first.json()["enquiry_id"]

    settings = Settings(
        database_url=postgres_db,
        session_secret="test-session-secret",
        slack_mode="sim",
        slack_base_url=sim,
        slack_token=TOKEN,
        slack_channel="#enquiry-ready",
    )
    notifications = _resolve_notifications(settings)
    clock.advance_by(3600)
    _drain(postgres_db, clock, FakeCrm(), notifications=notifications)

    rows = _notifications(postgres_db)
    assert len(rows) == 1
    assert rows[0].status == "sent"
    messages = _slack_messages(sim)
    assert len(messages) == 1
    assert messages[0].get("channel") == "#enquiry-ready"
    assert len(_events(postgres_db, "notification.sent")) == 1


def test_http_sim_definite_auth_failure_is_failed(sim, postgres_db: str) -> None:
    clock = FakeClock(T0)
    adapter = HttpNotificationAdapter(sim, "wrong-token")
    _ready_enquiry(postgres_db, clock, notifications=adapter)
    assert _slack_messages(sim) == []
    assert _notifications(postgres_db)[0].status == "failed"
    assert _notifications(postgres_db)[0].last_error_code == "auth_failed"
