"""A25 unit pins: submit_intake translates database failures only.

Database/transaction failures (SQLAlchemy errors) escaping the
single intake transaction become the typed ``IntakeUnavailableError``
the API maps to 503. Programming/configuration errors such as the
routing-owner ``ValueError`` and the idempotency conflict pass
through unchanged, so they keep their existing 500/409 handling.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import OperationalError

from app.config.routing import ROUTING_CONFIG
from app.services import intake


class _Clock:
    def now_utc(self) -> datetime:
        return datetime(2026, 10, 21, tzinfo=timezone.utc)


def _call(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(intake, "_submit_intake_once", _raise)
    intake.submit_intake(
        object(),  # type: ignore[arg-type]
        {},
        source="web",
        event_key=uuid.uuid4(),
        clock=_Clock(),  # type: ignore[arg-type]
        routing_config=ROUTING_CONFIG,
    )


def test_database_error_becomes_intake_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(intake.IntakeUnavailableError):
        _call(
            monkeypatch,
            OperationalError("INSERT", {}, ConnectionError("db down")),
        )


def test_value_error_passes_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="routing owner is not seeded"):
        _call(monkeypatch, ValueError("routing owner is not seeded: 'maya'"))


def test_idempotency_conflict_passes_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(intake.IdempotencyConflict):
        _call(monkeypatch, intake.IdempotencyConflict("conflict"))
