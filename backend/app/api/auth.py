"""Auth routes: login, session inspection and logout."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.db.session import get_session
from app.security import sessions as session_service
from app.security.passwords import verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Precomputed PBKDF2 string for a non-credential dummy password.
# Login verifies against this when no active operator row exists,
# so an unknown username costs the same PBKDF2 work as a wrong
# password and does not leak existence through timing. Nothing is
# hashed at import time; this is a literal.
_DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$210000$dAmCSxFPey2D971mOX5uwg==$"
    "FKsjVonXarvaym4vr+QFZWY9PvVM59CLnBrn7/sBhiA="
)

COOKIE_NAME = "leadops_session"
CSRF_HEADER = "X-CSRF-Token"
SESSION_MAX_AGE_SECONDS = 8 * 3600


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)


class SessionOut(BaseModel):
    operator_id: uuid.UUID
    username: str
    csrf_token: str
    expires_at: datetime


def _secret(request: Request) -> str:
    return request.app.state.settings.session_secret


def _require_session(request: Request, session: Session):
    token = request.cookies.get(COOKIE_NAME)
    resolved = session_service.resolve_session(session, token, _secret(request))
    if resolved is None:
        raise ApiError(401, "unauthenticated", "Authentication required")
    return token, resolved


@router.post("/login", response_model=SessionOut)
def login(
    payload: LoginInput,
    request: Request,
    session: Session = Depends(get_session),
):
    operator = session_service.get_active_operator(session, payload.username)
    # Always run one verification, against a dummy hash when the
    # operator row is missing, so timing cannot enumerate users.
    # The verification is evaluated into a local BEFORE the None
    # check: `operator is None or not verify_password(...)` would
    # short-circuit and skip the PBKDF2 work for unknown users.
    stored_hash = (
        operator.password_hash if operator is not None else _DUMMY_PASSWORD_HASH
    )
    password_ok = verify_password(payload.password, stored_hash)
    if operator is None or not password_ok:
        # One message for both failures: no user enumeration.
        raise ApiError(401, "invalid_credentials", "Invalid username or password")
    issued = session_service.issue_session(
        session, operator.id, _secret(request)
    )
    body = SessionOut(
        operator_id=operator.id,
        username=operator.username,
        csrf_token=issued.csrf_token,
        expires_at=issued.expires_at,
    )
    response = JSONResponse(content=body.model_dump(mode="json"))
    response.set_cookie(
        COOKIE_NAME,
        issued.token,
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return response


@router.get("/session", response_model=SessionOut)
def get_session_info(
    request: Request, session: Session = Depends(get_session)
):
    token, resolved = _require_session(request, session)
    return SessionOut(
        operator_id=resolved.operator_id,
        username=resolved.username,
        csrf_token=session_service.derive_csrf(token, _secret(request)),
        expires_at=resolved.expires_at,
    )


@router.post("/logout", status_code=204)
def logout(request: Request, session: Session = Depends(get_session)):
    token, _resolved = _require_session(request, session)
    supplied = request.headers.get(CSRF_HEADER) or ""
    expected = session_service.derive_csrf(token, _secret(request))
    if not session_service.csrf_matches(supplied, expected):
        raise ApiError(403, "csrf_invalid", "CSRF token is missing or invalid")
    session_service.revoke_session(session, token)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
