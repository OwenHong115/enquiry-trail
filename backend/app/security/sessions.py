"""Operator sessions with derived CSRF tokens.

The contract requires three things at once: the CSRF value is
stored only as a hash, the same value is returned at login and at
GET session, and comparisons are constant-time. Storing a random
CSRF token cannot satisfy retrieval-after-hash, so the token is
derived instead: csrf = HMAC-SHA256(SESSION_SECRET, raw session
token), hex-encoded. The raw session token never leaves the cookie;
the database stores only sha256(token) and sha256(csrf). Login and
GET session recompute the identical value from the cookie, and
revocation/expiry are checked against the database clock. The cost
of the derivation is that the CSRF secret is the session secret
rather than 32 independent random bytes; rotating SESSION_SECRET
invalidates every outstanding CSRF token (and should rotate with
the sessions themselves).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.orm import Session

from app.db import tables

SESSION_HOURS = 8


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class ResolvedSession:
    operator_id: uuid.UUID
    username: str
    expires_at: datetime


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def derive_csrf(raw_token: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), raw_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def get_active_operator(session: Session, username: str) -> Any | None:
    return session.execute(
        select(
            tables.operators.c.id,
            tables.operators.c.username,
            tables.operators.c.password_hash,
        ).where(
            tables.operators.c.username == username,
            tables.operators.c.active.is_(True),
        )
    ).one_or_none()


def issue_session(
    session: Session, operator_id: uuid.UUID, secret: str
) -> IssuedSession:
    token = secrets.token_urlsafe(32)
    csrf_token = derive_csrf(token, secret)
    row = session.execute(
        insert(tables.sessions)
        .values(
            token_hash=_hash(token),
            operator_id=operator_id,
            csrf_hash=_hash(csrf_token),
            expires_at=text(
                f"clock_timestamp() + interval '{SESSION_HOURS} hours'"
            ),
        )
        .returning(tables.sessions.c.expires_at)
    ).one()
    session.commit()
    return IssuedSession(
        token=token, csrf_token=csrf_token, expires_at=row.expires_at
    )


def resolve_session(
    session: Session, token: str | None, secret: str
) -> ResolvedSession | None:
    if not token:
        return None
    row = session.execute(
        select(
            tables.sessions.c.operator_id,
            tables.sessions.c.csrf_hash,
            tables.sessions.c.expires_at,
            tables.operators.c.username,
        )
        .join(
            tables.operators,
            tables.operators.c.id == tables.sessions.c.operator_id,
        )
        .where(
            tables.sessions.c.token_hash == _hash(token),
            tables.sessions.c.revoked_at.is_(None),
            tables.sessions.c.expires_at > text("clock_timestamp()"),
            tables.operators.c.active.is_(True),
        )
    ).one_or_none()
    if row is None:
        return None
    expected_csrf_hash = _hash(derive_csrf(token, secret))
    if not hmac.compare_digest(row.csrf_hash, expected_csrf_hash):
        return None
    return ResolvedSession(
        operator_id=row.operator_id,
        username=row.username,
        expires_at=row.expires_at,
    )


def revoke_session(session: Session, token: str) -> bool:
    result = session.execute(
        update(tables.sessions)
        .where(
            tables.sessions.c.token_hash == _hash(token),
            tables.sessions.c.revoked_at.is_(None),
        )
        .values(revoked_at=text("clock_timestamp()"))
    )
    session.commit()
    return result.rowcount > 0
