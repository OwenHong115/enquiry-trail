"""Enquiry read route and the shared enquiry-detail builder."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.auth import COOKIE_NAME
from app.api.display import format_display_no
from app.api.errors import ApiError, ErrorEnvelope
from app.db import enquiry_repo, health_repo
from app.db.session import get_session
from app.security import sessions as session_service

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


@dataclass(frozen=True)
class DetailStatus:
    """The four published status fields, as one mapped unit."""

    processing_summary: ProcessingSummary
    attention: Attention
    crm_status: CrmStatus
    next_attempt_at: datetime | None


# Internal sync-job state -> (summary, attention, crm.status).
# The "succeeded" row is handled separately below because its
# summary depends on prepared-and-synced.
_SYNC_STATE_MAP: dict[str | None, tuple[str, str, str]] = {
    None: ("processing", "none", "pending"),
    "pending": ("processing", "none", "pending"),
    "running": ("processing", "none", "syncing"),
    "waiting_retry": ("waiting_retry", "retry_scheduled", "waiting_retry"),
    "reconciling": ("reconciling", "none", "reconciling"),
    "blocked": ("blocked", "integration_blocked", "blocked"),
    "dead": ("failed", "intervention_required", "failed"),
}


def map_detail_status(
    *,
    sync_state: str | None,
    provider_blocked: bool,
    prepare_dead: bool,
    requires_review: bool,
    prepared_and_synced: bool,
    sync_next_attempt_at: datetime | None,
) -> DetailStatus:
    """Map internal state to the published detail statuses.

    The single place this mapping exists: build_enquiry_detail
    contains no state conditionals of its own. An unmapped sync
    state raises, so a new internal state can never leak through
    as a public status.
    """
    if prepared_and_synced:
        summary, attention, crm_status = "ready", "none", "synced"
    elif sync_state == "succeeded":
        summary, attention, crm_status = (
            "ready" if prepared_and_synced else "processing",
            "none",
            "synced",
        )
    elif sync_state in _SYNC_STATE_MAP:
        summary, attention, crm_status = _SYNC_STATE_MAP[sync_state]
    else:
        raise ValueError(f"unmapped sync job state: {sync_state!r}")

    # next_attempt_at is published ONLY for waiting_retry.
    next_attempt_at = (
        sync_next_attempt_at if sync_state == "waiting_retry" and not prepared_and_synced else None
    )

    # A pending review overrides only a base attention of "none":
    # it is never hidden by ready or processing, and never masks
    # integration_blocked, intervention_required or
    # retry_scheduled.
    if requires_review and attention == "none":
        attention = "review_required"

    # A blocked provider forces integration_blocked, and the
    # blocked summary when the job itself is blocked or absent.
    if provider_blocked and not prepared_and_synced and summary != "failed":
        attention = "integration_blocked"
        if sync_state in (None, "blocked"):
            summary = "blocked"

    # local_prepare_dead: this enquiry's own prepare job died, so
    # the summary is failed and the attention is an intervention;
    # crm fields still come from the sync row (or pending/null).
    if prepare_dead:
        summary = "failed"
        attention = "intervention_required"

    return DetailStatus(
        processing_summary=summary,  # type: ignore[arg-type]
        attention=attention,  # type: ignore[arg-type]
        crm_status=crm_status,  # type: ignore[arg-type]
        next_attempt_at=next_attempt_at,
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

    # Every published status comes from the one mapping; this
    # builder only assembles rows and mapped values.
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
            status=status.crm_status,
            remote_id=None,
            remote_url=None,
            synced_contact_version=0,
            next_attempt_at=status.next_attempt_at,
            # No link row: the sync job carries the diagnosis.
            last_error_code=job.last_error_code if job is not None else None,
        )
    else:
        crm = CrmOut(
            status=status.crm_status,
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
        processing_summary=processing_summary,
        attention=attention,
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
