"""Intake route (S7b skeleton; behaviour arrives in Green).

The request model mirrors the IntakeData contract exactly: extra
fields are forbidden and the email format check is a plain
validator (no extra dependency). The handler parses the request
and raises NotImplementedError until Green.
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.auth import COOKIE_NAME
from app.api.display import format_display_no
from app.api.errors import ApiError
from app.db.session import get_session
from app.domain.normalization import normalize_phone
from app.security import sessions as session_service
from app.services.intake import IdempotencyConflict, submit_intake

router = APIRouter(prefix="/api/v1/intake", tags=["intake"])

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class IntakeDataIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(min_length=1, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    phone: str | None = Field(default=None, min_length=1, max_length=40)
    postcode: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    service_type: Literal["bathroom", "kitchen", "general", "unsure"]
    message: str = Field(min_length=20, max_length=2000)
    preferred_contact: Literal["email", "phone"]
    acknowledge_demo: Literal[True]

    @field_validator("email")
    @classmethod
    def _email_format(cls, value: str) -> str:
        if not _EMAIL_PATTERN.match(value):
            raise ValueError("email must be a valid email address")
        return value


class IntakeReceiptOut(BaseModel):
    receipt_id: uuid.UUID
    enquiry_id: uuid.UUID
    display_no: str = Field(pattern=r"^ENQ-[0-9]{6,}$")
    accepted: Literal[True]
    replayed: bool
    deduplication: Literal["new", "content_duplicate"]
    status_url: str = Field(min_length=1, max_length=256)


@router.post("/form", status_code=202, response_model=IntakeReceiptOut)
def intake_form(
    payload: IntakeDataIn,
    request: Request,
    session: Session = Depends(get_session),
    idempotency_key: uuid.UUID = Header(..., alias="Idempotency-Key"),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    # 1. A live session is required before anything else.
    cookie_token = request.cookies.get(COOKIE_NAME)
    secret = request.app.state.settings.session_secret
    resolved = session_service.resolve_session(session, cookie_token, secret)
    if resolved is None:
        raise ApiError(401, "unauthenticated", "Authentication required")

    # 2. The CSRF token must match the one derived for this session.
    expected_csrf = session_service.derive_csrf(cookie_token, secret)
    if not session_service.csrf_matches(csrf_token, expected_csrf):
        raise ApiError(403, "csrf_invalid", "CSRF token is missing or invalid")

    # 3. Domain phone validation, once, before the service: only
    # this ValueError becomes a 422. Anything the service raises
    # (including its configuration ValueErrors) stays a 500.
    try:
        normalize_phone(payload.phone)
    except ValueError as err:
        raise ApiError(
            422,
            "validation_failed",
            "Request validation failed",
            field_errors=[{"path": "phone", "message": str(err)}],
        ) from err

    # 4-5. Persist; a key/payload conflict maps to 409. The auth
    # reads above opened a read transaction on this session; end it
    # so the service can own its single write transaction.
    session.rollback()
    try:
        result = submit_intake(
            session,
            payload.model_dump(),
            source="web",
            event_key=idempotency_key,
            clock=request.app.state.clock,
            routing_config=request.app.state.routing_config,
        )
    except IdempotencyConflict as err:
        raise ApiError(
            409, "idempotency_conflict", "Idempotency key conflict"
        ) from err

    # 6. The receipt.
    return IntakeReceiptOut(
        receipt_id=result.receipt_id,
        enquiry_id=result.enquiry_id,
        display_no=format_display_no(result.display_no),
        accepted=True,
        replayed=result.replayed,
        deduplication=result.deduplication,  # type: ignore[arg-type]
        status_url=f"/api/v1/enquiries/{result.enquiry_id}",
    )
