"""Production BusinessClock: aware UTC now from the system clock."""

from __future__ import annotations

from datetime import datetime, timezone


class SystemBusinessClock:
    """BusinessClock for production; business fields never use the
    database clock."""

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)
