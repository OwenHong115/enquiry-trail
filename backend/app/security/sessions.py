"""Operator session lifecycle seam (S7a; behaviour in Green)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: datetime


def issue_session(session: Session, operator_id: uuid.UUID) -> IssuedSession:
    """Create a persisted session for an operator."""
    raise NotImplementedError


def resolve_session(session: Session, token: str | None) -> uuid.UUID | None:
    """Return the operator id for a live session token, else None."""
    raise NotImplementedError


def revoke_session(session: Session, token: str) -> bool:
    """Revoke a session; True when one was live."""
    raise NotImplementedError
