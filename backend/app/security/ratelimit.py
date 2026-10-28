"""Fixed-window database rate limiting (P3b, A27).

Counters live in ``leadops.rate_limit_counters`` keyed by
``(scope, subject_hash, window_start)``. The subject is only ever
the SHA-256 hash of the trusted-proxy-aware client IP, never the
raw address. Window boundaries and expiry are computed from the
database clock (``clock_timestamp()``), never the business clock,
matching the lease and session-expiry rule in AGENTS.md.

The same table, under a distinct ``webhook`` scope, also stores
one row per accepted webhook signature so a replayed signed
delivery is rejected independently of intake idempotency, which
is keyed by ``(source, event_key)`` in the receipts table.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

WEBHOOK_SCOPE = "webhook"


def subject_hash(client_ip: str | None) -> str:
    """SHA-256 hex digest of a client IP (``unknown`` when absent)."""
    return hashlib.sha256((client_ip or "unknown").encode("utf-8")).hexdigest()


def check_rate_limit(
    session: Session,
    *,
    scope: str,
    client_ip: str | None,
    limit: int,
    window_seconds: int,
) -> tuple[bool, float]:
    """Increment the current fixed window; return (allowed, retry_after).

    The increment is committed immediately so a rejected or failed
    downstream attempt still counts, and so the session is left
    clean for the handler that follows.
    """
    subject = subject_hash(client_ip)
    row = session.execute(
        text(
            """
            WITH clock AS (SELECT clock_timestamp() AS now),
            win AS (
                SELECT to_timestamp(
                    floor(extract(epoch FROM (SELECT now FROM clock)) / :window)
                    * :window
                ) AS window_start
            )
            INSERT INTO leadops.rate_limit_counters
                (scope, subject_hash, window_start, count, expires_at)
            SELECT :scope, :subject, win.window_start, 1,
                   win.window_start + make_interval(secs => :window)
            FROM win
            ON CONFLICT (scope, subject_hash, window_start)
            DO UPDATE SET count = rate_limit_counters.count + 1
            RETURNING count,
                      expires_at,
                      (SELECT now FROM clock) AS db_now
            """
        ),
        {"scope": scope, "subject": subject, "window": window_seconds},
    ).one()
    session.commit()
    count, expires_at, db_now = int(row[0]), row[1], row[2]
    if count <= limit:
        return True, 0.0
    retry_after = max(0.0, (expires_at - db_now).total_seconds())
    return False, retry_after


def claim_webhook_signature(
    session: Session,
    *,
    signature: str,
    timestamp: int,
    replay_window_seconds: int,
) -> bool:
    """Record one webhook signature; True only for its first delivery.

    The row is keyed by the hash of the full signature under the
    webhook scope, with the delivery timestamp as its window start
    and an expiry one replay window later. A second delivery of the
    same signature increments the counter and is reported as a
    replay, so it creates no enquiry even under a fresh
    Idempotency-Key.
    """
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    window_start = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    expires_at = window_start + timedelta(seconds=replay_window_seconds)
    row = session.execute(
        text(
            """
            INSERT INTO leadops.rate_limit_counters
                (scope, subject_hash, window_start, count, expires_at)
            VALUES (:scope, :subject, :window_start, 1, :expires_at)
            ON CONFLICT (scope, subject_hash, window_start)
            DO UPDATE SET count = rate_limit_counters.count + 1
            RETURNING count
            """
        ),
        {
            "scope": WEBHOOK_SCOPE,
            "subject": digest,
            "window_start": window_start,
            "expires_at": expires_at,
        },
    ).one()
    session.commit()
    return int(row[0]) == 1


def database_now(session: Session) -> datetime:
    """Current database-clock time (rate limits, webhook freshness)."""
    now = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    session.rollback()
    return now
