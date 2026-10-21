"""FakeScheduler: deterministic backoff 1/4/16 with zero jitter."""

from __future__ import annotations

from datetime import datetime

_BACKOFF_SECONDS = (1.0, 4.0, 16.0)


class FakeScheduler:
    """Scheduler test implementation; never sleeps, never random."""

    def compute_backoff(self, attempt_no: int, jitter: float = 0.0) -> float:
        index = min(max(attempt_no, 1), len(_BACKOFF_SECONDS)) - 1
        return _BACKOFF_SECONDS[index] + jitter

    def parse_retry_after(self, value: str | None, now: datetime) -> float:
        if value is None:
            return 0.0
        try:
            return float(value)
        except ValueError:
            return 0.0
