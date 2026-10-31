"""NotificationPort: the Slack notification boundary (no HTTP here)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

_NOTIFICATION_ERROR_CODES = frozenset(
    {
        "connect_failed",
        "rate_limited",
        "unavailable",
        "auth_failed",
        "validation_failed",
        "not_found",
        "conflict",
        "result_unknown",
        "invalid_response",
        "director_unavailable",
    }
)

NOTIFICATION_ERROR_CODES = _NOTIFICATION_ERROR_CODES


@dataclass(frozen=True)
class NotificationContext:
    """Per-call context carried to every notification operation."""

    job_id: uuid.UUID
    cycle_no: int
    attempt_no: int
    deadline_seconds: float = 15.0
    external_operation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ReadyNotification:
    """One enquiry-ready notification to deliver exactly once."""

    enquiry_id: uuid.UUID
    operation_key: str
    text: str
    channel: str | None = None


@dataclass(frozen=True)
class NotificationSent:
    message_id: str


class NotificationError(Exception):
    """A classified notification provider failure."""

    def __init__(
        self,
        code: str,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        if code not in _NOTIFICATION_ERROR_CODES:
            raise ValueError(f"unknown notification error code: {code!r}")
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


class NotificationPort(Protocol):
    """The swappable notification interface the service depends on."""

    def send_ready(
        self, message: ReadyNotification, context: NotificationContext
    ) -> NotificationSent: ...


# Convenient aliases mirroring the CRM port vocabulary.
SendContext = NotificationContext
NotificationMessage = ReadyNotification
SendResult = NotificationSent
SlackPort = NotificationPort
