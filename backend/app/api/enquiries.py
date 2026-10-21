"""Enquiry read route (S7c skeleton; behaviour arrives in Green)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_session

router = APIRouter(prefix="/api/v1/enquiries", tags=["enquiries"])


class OwnerOut(BaseModel):
    id: uuid.UUID
    code: str
    display_name: str


class ContactOut(BaseModel):
    id: uuid.UUID
    email: str
    first_name: str
    last_name: str | None
    phone_e164: str | None
    version: int


class CrmOut(BaseModel):
    status: str
    remote_id: str | None
    remote_url: str | None
    synced_contact_version: int
    next_attempt_at: datetime | None
    last_error_code: str | None


class TaskOut(BaseModel):
    id: uuid.UUID
    enquiry_id: uuid.UUID
    kind: str
    title: str
    owner: OwnerOut
    status: str
    due_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    version: int


class NotificationOut(BaseModel):
    status: str
    notification_id: uuid.UUID | None
    last_error_code: str | None


class EnquiryDetailOut(BaseModel):
    id: uuid.UUID
    display_no: str
    contact: ContactOut
    service_type: str
    postcode: str | None
    owner: OwnerOut | None
    processing_summary: str
    attention: str
    requires_review: bool
    crm: CrmOut
    received_at: datetime
    version: int
    message: str
    preferred_contact: str
    scenario_run_id: uuid.UUID | None
    routing_reason: str | None
    rules_hash: str
    review_reason_codes: list[str]
    review_resolution_note: str | None
    task: TaskOut | None
    enrichment: dict | None
    draft: dict | None
    notification: NotificationOut
    local_prepared_at: datetime | None
    required_contact_version: int
    related_jobs: list


@router.get("/{enquiry_id}", response_model=EnquiryDetailOut)
def get_enquiry(
    enquiry_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
):
    raise NotImplementedError
