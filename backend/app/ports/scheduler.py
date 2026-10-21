"""Scheduler port: backoff and Retry-After computations."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Scheduler(Protocol):
    """Computes retry delays; implementations never sleep."""

    def compute_backoff(self, attempt_no: int, jitter: float) -> float:
        """Return the delay in seconds before the next attempt."""
        ...

    def parse_retry_after(self, value: str | None, now: datetime) -> float:
        """Return the Retry-After delay in seconds for a raw value."""
        ...
