"""FakeNotifications: scripted NotificationPort for tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ports.notifications import (
    NotificationContext,
    NotificationSent,
    ReadyNotification,
)


@dataclass(frozen=True)
class RecordedNotificationCall:
    method: str
    fields: dict[str, Any]
    context: NotificationContext


@dataclass
class FakeNotifications:
    """Scripted notification port; records every call.

    ``error`` raises before any remote write is recorded.
    ``commit_then_error`` records the remote write first and then
    raises, modelling a success response lost on the wire (A20).
    """

    error: BaseException | None = None
    commit_then_error: BaseException | None = None
    calls: list[RecordedNotificationCall] = field(default_factory=list)
    sent: list[ReadyNotification] = field(default_factory=list)

    @property
    def sent_count(self) -> int:
        return len(self.sent)

    def calls_of(self, method: str) -> list[RecordedNotificationCall]:
        return [c for c in self.calls if c.method == method]

    def send_ready(
        self, message: ReadyNotification, context: NotificationContext
    ) -> NotificationSent:
        self.calls.append(
            RecordedNotificationCall(
                "send_ready",
                {
                    "enquiry_id": message.enquiry_id,
                    "operation_key": message.operation_key,
                    "text": message.text,
                },
                context,
            )
        )
        if self.error is not None:
            raise self.error
        self.sent.append(message)
        if self.commit_then_error is not None:
            raise self.commit_then_error
        return NotificationSent(message_id=f"fake-msg-{len(self.sent)}")


# Aliases for convenience at call sites.
FakeNotificationPort = FakeNotifications
FakeSlack = FakeNotifications
