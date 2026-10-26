"""Enquiry read routes and the shared enquiry-detail builder."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import COOKIE_NAME
from app.api.deps import require_authenticated
from app.api.display import format_display_no
from app.api.errors import ApiError, ErrorEnvelope
from app.db import enquiry_repo, health_repo
from app.db.session import get_session
from app.security import sessions as session_service
from app.services.read_model import (
    DetailStatus,
    InvalidCursor,
    decode_cursor,
    encode_cursor,
    map_detail_status,
)
from app.services import read_model

__all__ = ["DetailStatus", "map_detail_status"]

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


# The published status vocabulary (contract enums): internal
# job states are never published verbatim.
ProcessingSummary = Literal[
    "processing", "waiting_retry", "blocked", "reconciling", "ready", "failed"
]
Attention = Literal[
    "none",
    "review_required",
    "retry_scheduled",
    "notification_uncertain",
    "integration_blocked",
    "intervention_required",
]
CrmStatus = Literal[
    "pending",
    "syncing",
    "waiting_retry",
    "reconciling",
    "synced",
    "blocked",
    "failed",
]


class CrmOut(BaseModel):
    status: CrmStatus
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


class RelatedJobOut(BaseModel):
    id: uuid.UUID
    kind: str
    state: str
    updated_at: datetime


class EnquiryDetailOut(BaseModel):
    id: uuid.UUID
    display_no: str
    contact: ContactOut
    service_type: str
    postcode: str | None
    owner: OwnerOut | None
    processing_summary: ProcessingSummary
    attention: Attention
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
    related_jobs: list[RelatedJobOut]


class EnquiryListItemOut(BaseModel):
    """Strict subset of the detail fields (see contract-deviations)."""

    id: uuid.UUID
    display_no: str
    received_at: datetime
    service_type: str
    processing_summary: ProcessingSummary
    attention: Attention
    crm_status: CrmStatus
    next_attempt_at: datetime | None
    owner: OwnerOut | None
    contact: ContactOut


class EnquiryListOut(BaseModel):
    items: list[EnquiryListItemOut]
    next_cursor: str | None


def task_out_from_row(task_row) -> TaskOut:
    """Shape a TaskOut from a task row joined with its owner."""
    return TaskOut(
        id=task_row.id,
        enquiry_id=task_row.enquiry_id,
        kind=task_row.kind,
        title=task_row.title,
        owner=OwnerOut(
            id=task_row.owner_id,
            code=task_row.owner_code,
            display_name=task_row.owner_display_name,
        ),
        status=task_row.status,
        due_at=task_row.due_at,
        completed_at=task_row.completed_at,
        cancelled_at=task_row.cancelled_at,
        version=task_row.version,
    )


def build_enquiry_detail(
    session: Session, enquiry_id: uuid.UUID
) -> EnquiryDetailOut | None:
    """Build the EnquiryDetailOut for an enquiry, or None if absent.

    Shared by the detail route and the review-resolve response so
    both endpoints shape the detail identically.
    """
    detail = enquiry_repo.get_enquiry_detail(session, enquiry_id)
    if detail is None:
        return None

    link = enquiry_repo.get_crm_link(session, detail.contact_id)
    job = enquiry_repo.get_sync_job_for_contact(session, detail.contact_id)
    prepare_job = enquiry_repo.get_prepare_job(session, enquiry_id)
    health = health_repo.get_health(session, "hubspot")
    task_row = enquiry_repo.get_follow_up_task(session, enquiry_id)

    prepared_and_synced = (
        detail.local_prepared_at is not None
        and link is not None
        and link.sync_status == "synced"
        and link.synced_contact_version >= detail.required_contact_version
    )

    # Every published status comes from the shared read model;
    # this builder only assembles rows and mapped values.
    status = map_detail_status(
        sync_state=job.state if job is not None else None,
        provider_blocked=health is not None and health.status == "blocked",
        prepare_dead=prepare_job is not None and prepare_job.state == "dead",
        requires_review=detail.requires_review,
        prepared_and_synced=prepared_and_synced,
        sync_next_attempt_at=job.next_attempt_at if job is not None else None,
    )
    processing_summary = status.processing_summary
    attention = status.attention

    if link is None:
        crm = CrmOut(
            status=status.crm_status,  # type: ignore[arg-type]
            remote_id=None,
            remote_url=None,
            synced_contact_version=0,
            next_attempt_at=status.next_attempt_at,
            # No link row: the sync job carries the diagnosis.
            last_error_code=job.last_error_code if job is not None else None,
        )
    else:
        crm = CrmOut(
            status=status.crm_status,  # type: ignore[arg-type]
            remote_id=link.remote_id,
            remote_url=None,
            synced_contact_version=link.synced_contact_version,
            next_attempt_at=status.next_attempt_at,
            last_error_code=link.last_error_code,
        )

    task = task_out_from_row(task_row) if task_row is not None else None

    return EnquiryDetailOut(
        id=detail.id,
        display_no=format_display_no(detail.display_no),
        contact=ContactOut(
            id=detail.contact_id,
            email=detail.email_norm,
            first_name=detail.first_name,
            last_name=detail.last_name,
            phone_e164=detail.phone_e164,
            version=detail.contact_version,
        ),
        service_type=detail.service_type,
        postcode=detail.postcode,
        owner=(
            OwnerOut(
                id=detail.owner_id,
                code=detail.owner_code,
                display_name=detail.owner_display_name,
            )
            if detail.owner_id is not None
            else None
        ),
        processing_summary=processing_summary,  # type: ignore[arg-type]
        attention=attention,  # type: ignore[arg-type]
        requires_review=detail.requires_review,
        crm=crm,
        received_at=detail.received_at,
        version=detail.version,
        message=detail.message,
        preferred_contact=detail.preferred_contact,
        scenario_run_id=detail.scenario_run_id,
        routing_reason=detail.routing_reason,
        rules_hash=detail.rules_hash,
        review_reason_codes=detail.review_reason_codes,
        review_resolution_note=detail.resolution_note,
        task=task,
        enrichment=None,
        draft=None,
        notification=NotificationOut(
            status="disabled", notification_id=None, last_error_code=None
        ),
        local_prepared_at=detail.local_prepared_at,
        required_contact_version=detail.required_contact_version,
        related_jobs=[RelatedJobOut(id=row.id, kind=row.kind, state=row.state,
                                   updated_at=row.updated_at)
                      for row in enquiry_repo.get_related_jobs(
                          session, enquiry_id, detail.contact_id)],
    )


def _decode_or_422(cursor: str | None):
    if cursor is None:
        return None
    try:
        return decode_cursor(cursor)
    except InvalidCursor as err:
        raise ApiError(
            422, "validation_failed", "Invalid cursor",
            field_errors=[{"path": "cursor", "message": "invalid cursor"}],
        ) from err


@router.get(
    "",
    response_model=EnquiryListOut,
    responses={401: {"model": ErrorEnvelope}, 422: {"model": ErrorEnvelope}},
)
def list_enquiries_route(
    request: Request,
    session: Session = Depends(get_session),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
    processing_summary: ProcessingSummary | None = Query(default=None),
    attention: Attention | None = Query(default=None),
    owner: str | None = Query(default=None),
    q: str | None = Query(default=None),
):
    require_authenticated(request, session)
    items, next_key = read_model.list_enquiries(
        session,
        limit=limit,
        cursor=_decode_or_422(cursor),
        processing_summary=processing_summary,
        attention=attention,
        owner_code=owner,
        q=q,
    )
    return EnquiryListOut(
        items=[
            EnquiryListItemOut(
                id=item.id,
                display_no=format_display_no(item.display_no),
                received_at=item.received_at,
                service_type=item.service_type,
                processing_summary=item.status.processing_summary,  # type: ignore[arg-type]
                attention=item.status.attention,  # type: ignore[arg-type]
                crm_status=item.status.crm_status,  # type: ignore[arg-type]
                next_attempt_at=item.status.next_attempt_at,
                owner=(
                    OwnerOut(
                        id=item.owner.owner_id,
                        code=item.owner.owner_code,
                        display_name=item.owner.owner_display_name,
                    )
                    if item.owner.owner_id is not None
                    else None
                ),
                contact=ContactOut(
                    id=item.contact.contact_id,
                    email=item.contact.email_norm,
                    first_name=item.contact.first_name,
                    last_name=item.contact.last_name,
                    phone_e164=item.contact.phone_e164,
                    version=item.contact.contact_version,
                ),
            )
            for item in items
        ],
        next_cursor=encode_cursor(next_key) if next_key is not None else None,
    )


@router.get(
    "/{enquiry_id}",
    response_model=EnquiryDetailOut,
    # Schema metadata only: enforcement stays in the route's
    # session resolution.
    responses={
        401: {"model": ErrorEnvelope},
        404: {"model": ErrorEnvelope},
    },
)
def get_enquiry(
    enquiry_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
):
    # A live session is required before any lookup (safe method:
    # no CSRF token is needed).
    secret = request.app.state.settings.session_secret
    token = request.cookies.get(COOKIE_NAME)
    if session_service.resolve_session(session, token, secret) is None:
        raise ApiError(401, "unauthenticated", "Authentication required")

    detail = build_enquiry_detail(session, enquiry_id)
    if detail is None:
        raise ApiError(404, "not_found", "Enquiry not found")
    return detail
