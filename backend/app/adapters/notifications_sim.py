"""HTTP Slack notification adapter: one request per call, no retries.

Mirrors the house style of ``adapters/crm_http.py``: every transport
error is mapped to NotificationError and httpx exceptions never leak.
A 5xx on a write is ``result_unknown`` (the provider may have
committed before the response was lost); definite client errors keep
their classified code.
"""

from __future__ import annotations

import httpx

from app.logging import log_event
from app.ports.notifications import (
    NotificationContext,
    NotificationError,
    NotificationSent,
    ReadyNotification,
)


def _validate_base_url(base_url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("SLACK_BASE_URL must be an http(s) URL with a host")
    return base_url.rstrip("/")


class HttpNotificationAdapter:
    """NotificationPort over HTTP (Slack-like message endpoint)."""

    def __init__(
        self, base_url: str, token: str, client: httpx.Client | None = None
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        if not token.strip():
            raise ValueError("Slack token must not be empty")
        self._token = token
        self._client = client
        self._owns_client = client is None

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()

    def __enter__(self) -> "HttpNotificationAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        if status == 404:
            raise NotificationError("not_found", http_status=404)
        if status in (400, 422):
            raise NotificationError("validation_failed", http_status=status)
        if status in (401, 403):
            raise NotificationError("auth_failed", http_status=status)
        if status == 409:
            raise NotificationError("conflict", http_status=409)
        if status == 429:
            raise NotificationError(
                "rate_limited", http_status=429,
                retry_after_seconds=self._retry_after(response),
            )
        if status >= 500:
            raise NotificationError("result_unknown", http_status=status)
        raise NotificationError("invalid_response", http_status=status)

    def send_ready(
        self, message: ReadyNotification, context: NotificationContext
    ) -> NotificationSent:
        timeout_seconds = context.deadline_seconds if context.deadline_seconds > 0 else 15.0
        timeout = httpx.Timeout(timeout_seconds)
        body: dict = {
            "operation_key": message.operation_key,
            "enquiry_id": str(message.enquiry_id),
            "text": message.text,
        }
        if message.channel is not None:
            body["channel"] = message.channel
        client = self._client or httpx.Client(timeout=timeout)
        try:
            try:
                response = client.post(
                    f"{self._base_url}/slack/messages",
                    json=body,
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=timeout,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise NotificationError("connect_failed") from exc
            except httpx.TimeoutException as exc:
                raise NotificationError("result_unknown") from exc
            except httpx.HTTPError as exc:
                raise NotificationError("result_unknown") from exc
        finally:
            if self._client is None:
                client.close()
        log_event(
            "slack.http", job_id=str(context.job_id),
            method="POST", status=str(response.status_code),
        )
        self._raise_for_status(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise NotificationError("invalid_response") from exc
        if not isinstance(data, dict) or not (data.get("id") or data.get("message_id")):
            raise NotificationError("invalid_response")
        return NotificationSent(message_id=str(data.get("id") or data.get("message_id")))


# Aliases in the provider vocabulary.
SlackHttpAdapter = HttpNotificationAdapter
HttpSlackAdapter = HttpNotificationAdapter
SimNotificationAdapter = HttpNotificationAdapter
