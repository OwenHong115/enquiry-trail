"""Production Scheduler: contract backoff with bounded jitter."""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

_BACKOFF_SECONDS = (1.0, 4.0, 16.0)
RETRY_AFTER_MAX_SECONDS = 300.0


class BackoffScheduler:
    """Backoff 1/4/16 plus a uniform draw inside [0, jitter_max]."""

    def __init__(self, draw: Callable[[], float] = random.random) -> None:
        self._draw = draw

    def compute_backoff(self, attempt_no: int, jitter: float) -> float:
        index = min(max(attempt_no, 1), len(_BACKOFF_SECONDS)) - 1
        return _BACKOFF_SECONDS[index] + self._draw() * jitter

    def parse_retry_after(self, value: str | None, now: datetime) -> float:
        # Raw provider values are interpreted in exactly one place,
        # including the worker contract's 300-second cap.
        if value is None:
            return 0.0
        text = value.strip()
        try:
            seconds = float(text)
        except ValueError:
            try:
                at = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                return 0.0
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            seconds = (at - now).total_seconds()
        return min(max(seconds, 0.0), RETRY_AFTER_MAX_SECONDS)
