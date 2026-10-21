"""Fake BusinessClock: fixed start instant with explicit advance."""

from __future__ import annotations

from datetime import datetime, timedelta


class FakeClock:
    """BusinessClock test implementation; time moves only when told."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock start must be timezone-aware")
        self._now = start

    def now_utc(self) -> datetime:
        return self._now

    def advance_by(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    def advance_to(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FakeClock instant must be timezone-aware")
        self._now = instant
