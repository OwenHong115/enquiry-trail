"""Unit tests for the configured Slack channel wiring seam."""

from __future__ import annotations

import json
import uuid

import httpx

from app.ports.notifications import NotificationContext, ReadyNotification
from app.settings import Settings
from app.worker.runner import _resolve_notifications

DB = "postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops"


def _ctx() -> NotificationContext:
    return NotificationContext(
        job_id=uuid.uuid4(), cycle_no=1, attempt_no=1, deadline_seconds=15.0
    )


def _msg() -> ReadyNotification:
    return ReadyNotification(
        enquiry_id=uuid.uuid4(),
        operation_key="slack:notify_ready:test",
        text="Enquiry ready",
    )


def _capture_client(captured: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(201, json={"id": "msg-1"})

    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url="http://sim"
    )


def _inject_client(port, client: httpx.Client) -> None:
    target = getattr(port, "_inner", port)
    target._client = client  # type: ignore[attr-defined]
    target._owns_client = False  # type: ignore[attr-defined]


def test_resolved_notifications_carry_configured_channel() -> None:
    settings = Settings(
        database_url=DB,
        session_secret="s",
        slack_mode="sim",
        slack_base_url="http://sim",
        slack_token="tok",
        slack_channel="#enquiry-ready",
    )
    port = _resolve_notifications(settings)
    assert port is not None
    captured: dict = {}
    _inject_client(port, _capture_client(captured))
    port.send_ready(_msg(), _ctx())
    assert captured.get("channel") == "#enquiry-ready"


def test_resolved_notifications_omit_channel_when_unconfigured() -> None:
    settings = Settings(
        database_url=DB,
        session_secret="s",
        slack_mode="sim",
        slack_base_url="http://sim",
        slack_token="tok",
    )
    port = _resolve_notifications(settings)
    assert port is not None
    captured: dict = {}
    _inject_client(port, _capture_client(captured))
    port.send_ready(_msg(), _ctx())
    assert "channel" not in captured


def test_resolved_notifications_disabled_returns_none() -> None:
    settings = Settings(database_url=DB, session_secret="s")
    assert _resolve_notifications(settings) is None
