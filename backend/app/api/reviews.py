"""Review-resolution route (S7d stub; behaviour arrives in Green).

The request model mirrors the ReviewResolve contract. Session and
CSRF are enforced here, before the handler, so those failures are
already observable; the resolution itself is not implemented yet.
"""

from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.auth import COOKIE_NAME
from app.api.enquiries import EnquiryDetailOut
from app.api.errors import ApiError
from app.db.session import get_session
from app.security import sessions as session_service

router = APIRouter(prefix="/api/v1/enquiries", tags=["reviews"])


class ReviewResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    resolution_note: str = Field(min_length=10, max_length=500)


def require_operator(
    request: Request, session: Session, csrf_token: str | None
):
    """Resolve the session, then check CSRF (401 before 403)."""
    cookie_token = request.cookies.get(COOKIE_NAME)
    secret = request.app.state.settings.session_secret
    resolved = session_service.resolve_session(session, cookie_token, secret)
    if resolved is None:
        raise ApiError(401, "unauthenticated", "Authentication required")
    expected_csrf = session_service.derive_csrf(cookie_token, secret)
    if not csrf_token or not hmac.compare_digest(csrf_token, expected_csrf):
        raise ApiError(403, "csrf_invalid", "CSRF token is missing or invalid")
    return resolved


@router.post(
    "/{enquiry_id}/review/resolve", response_model=EnquiryDetailOut
)
def resolve_review_route(
    enquiry_id: uuid.UUID,
    payload: ReviewResolveIn,
    request: Request,
    session: Session = Depends(get_session),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    require_operator(request, session, csrf_token)
    raise NotImplementedError
