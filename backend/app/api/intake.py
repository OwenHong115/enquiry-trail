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

from app.db.session import get_session

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
    raise NotImplementedError
