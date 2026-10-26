"""Shared API dependencies for operator write actions."""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from app.api.auth import COOKIE_NAME
from app.api.errors import ApiError
from app.security import sessions as session_service


def require_authenticated(request: Request, session: Session):
    """Resolve a live session for a safe read (no CSRF needed)."""
    cookie_token = request.cookies.get(COOKIE_NAME)
    secret = request.app.state.settings.session_secret
    resolved = session_service.resolve_session(session, cookie_token, secret)
    if resolved is None:
        raise ApiError(401, "unauthenticated", "Authentication required")
    return resolved


def require_operator(
    request: Request, session: Session, csrf_token: str | None
):
    """Resolve the session, then check CSRF (401 before 403).

    Returns the resolved session, whose operator_id identifies the
    actor for audited writes. Auth reads open a read transaction on
    the session; callers must roll it back before handing the
    session to a service that opens its own transaction.
    """
    cookie_token = request.cookies.get(COOKIE_NAME)
    secret = request.app.state.settings.session_secret
    resolved = session_service.resolve_session(session, cookie_token, secret)
    if resolved is None:
        raise ApiError(401, "unauthenticated", "Authentication required")
    expected_csrf = session_service.derive_csrf(cookie_token, secret)
    if not session_service.csrf_matches(csrf_token, expected_csrf):
        raise ApiError(403, "csrf_invalid", "CSRF token is missing or invalid")
    return resolved
