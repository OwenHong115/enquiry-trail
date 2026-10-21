"""BusinessClock port: the single source of business time."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class BusinessClock(Protocol):
    """Provides the current business instant as an aware datetime."""

    def now_utc(self) -> datetime:
        """Return the current business time (timezone-aware, UTC)."""
        ...
