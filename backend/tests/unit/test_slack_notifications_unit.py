"""Unit tests for Slack notification settings and adapter mapping."""

from __future__ import annotations

import uuid

import httpx
import pytest

from app.adapters.notifications_sim import HttpNotificationAdapter
from app.ports.notifications import (
    NotificationContext,
    NotificationError,
    ReadyNotification,
)
from app.settings import Settings

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


def test_slack_disabled_by_default_requires_nothing() -> None:
    s = Settings(database_url=DB, session_secret="s")
    assert s.slack_mode == "disabled"


def test_slack_sim_requires_endpoint_and_token() -> None:
    with pytest.raises(ValueError):
        Settings(database_url=DB, session_secret="s", slack_mode="sim")
    with pytest.raises(ValueError):
        Settings(
            database_url=DB, session_secret="s", slack_mode="sim",
            slack_base_url="http://127.0.0.1:8091",
        )


def test_slack_http_requires_endpoint_and_token() -> None:
    with pytest.raises(ValueError):
        Settings(database_url=DB, session_secret="s", slack_mode="http", slack_token="t")


def test_slack_bad_mode_and_bad_url_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(database_url=DB, session_secret="s", slack_mode="bogus")
    with pytest.raises(ValueError):
        Settings(
            database_url=DB, session_secret="s", slack_mode="sim",
            slack_base_url="ftp://example.com", slack_token="t",
        )


def test_slack_enabled_modes_construct() -> None:
    for mode in ("sim", "http"):
        s = Settings(
            database_url=DB, session_secret="s", slack_mode=mode,
            slack_base_url="http://127.0.0.1:8091", slack_token="tok",
        )
        assert s.slack_mode == mode


def test_notification_error_code_vocabulary() -> None:
    with pytest.raises(ValueError):
        NotificationError("not_a_real_code")


def _adapter(handler) -> HttpNotificationAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://sim")
    return HttpNotificationAdapter("http://sim", "tok", client=client)


def test_adapter_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/slack/messages"
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(201, json={"id": "msg-1"})

    result = _adapter(handler).send_ready(_msg(), _ctx())
    assert result.message_id == "msg-1"


def test_adapter_auth_is_definite_failure() -> None:
    adapter = _adapter(lambda r: httpx.Response(401, json={"error": "unauthorized"}))
    with pytest.raises(NotificationError) as exc:
        adapter.send_ready(_msg(), _ctx())
    assert exc.value.code == "auth_failed"


def test_adapter_validation_is_definite_failure() -> None:
    adapter = _adapter(lambda r: httpx.Response(422, json={"error": "validation_failed"}))
    with pytest.raises(NotificationError) as exc:
        adapter.send_ready(_msg(), _ctx())
    assert exc.value.code == "validation_failed"


def test_adapter_500_on_write_is_result_unknown() -> None:
    adapter = _adapter(lambda r: httpx.Response(500, json={"error": "internal_after_commit"}))
    with pytest.raises(NotificationError) as exc:
        adapter.send_ready(_msg(), _ctx())
    assert exc.value.code == "result_unknown"


def test_adapter_429_carries_retry_after() -> None:
    adapter = _adapter(
        lambda r: httpx.Response(429, json={"error": "rate_limited"}, headers={"Retry-After": "7"})
    )
    with pytest.raises(NotificationError) as exc:
        adapter.send_ready(_msg(), _ctx())
    assert exc.value.code == "rate_limited"
    assert exc.value.retry_after_seconds == 7
