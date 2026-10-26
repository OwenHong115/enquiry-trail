"""Shared read model: detail status projection and list queries.

The detail endpoint and the enquiry/task lists use this single
status mapping and batched query path, so a list item can never
disagree with the detail for the same enquiry. SQL lives in
app/db/list_repo.py; this module holds decisions and shaping.
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db import health_repo, list_repo

PROCESSING_SUMMARIES = (
    "processing", "waiting_retry", "blocked", "reconciling", "ready", "failed",
)
ATTENTIONS = (
    "none", "review_required", "retry_scheduled", "notification_uncertain",
    "integration_blocked", "intervention_required",
)
TASK_STATUSES = ("open", "completed", "cancelled")


@dataclass(frozen=True)
class DetailStatus:
    """The four published status fields, as one mapped unit."""

    processing_summary: str
    attention: str
    crm_status: str
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

    The single place this mapping exists: the detail builder and
    the lists contain no state conditionals of their own. An
    unmapped sync state raises, so a new internal state can never
    leak through as a public status.
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
        processing_summary=summary,
        attention=attention,
        crm_status=crm_status,
        next_attempt_at=next_attempt_at,
    )


def status_for_row(row: Any, *, provider_blocked: bool) -> DetailStatus:
    """Project one batched enquiry row to its published status."""
    prepared_and_synced = (
        row.local_prepared_at is not None
        and row.link_sync_status == "synced"
        and row.synced_contact_version is not None
        and row.synced_contact_version >= row.required_contact_version
    )
    return map_detail_status(
        sync_state=row.sync_state,
        provider_blocked=provider_blocked,
        prepare_dead=row.prepare_state == "dead",
        requires_review=bool(row.requires_review),
        prepared_and_synced=prepared_and_synced,
        sync_next_attempt_at=row.sync_next_attempt_at,
    )


class InvalidCursor(ValueError):
    """A pagination cursor that cannot be decoded or parsed."""


@dataclass(frozen=True)
class CursorKey:
    created_at: datetime
    id: uuid.UUID


def encode_cursor(key: CursorKey) -> str:
    raw = json.dumps(
        {"t": key.created_at.astimezone(timezone.utc).isoformat(),
         "id": str(key.id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(raw: str) -> CursorKey:
    try:
        decoded = base64.b64decode(
            raw.encode("ascii"), altchars=b"-_", validate=True
        )
        data = json.loads(decoded.decode("utf-8"))
        created = datetime.fromisoformat(data["t"])
        if created.tzinfo is None:
            raise ValueError("cursor timestamp has no timezone")
        return CursorKey(created_at=created, id=uuid.UUID(data["id"]))
    except Exception as err:
        raise InvalidCursor("invalid cursor") from err


@dataclass(frozen=True)
class EnquiryListItem:
    id: uuid.UUID
    display_no: int
    received_at: datetime
    service_type: str
    owner: Any
    contact: Any
    status: DetailStatus


@dataclass(frozen=True)
class TaskListItem:
    row: Any


def _provider_blocked(session: Session) -> bool:
    health = health_repo.get_health(session, "hubspot")
    return health is not None and health.status == "blocked"


_BATCH = 200


def list_enquiries(
    session: Session,
    *,
    limit: int,
    cursor: CursorKey | None,
    processing_summary: str | None = None,
    attention: str | None = None,
    owner_code: str | None = None,
    q: str | None = None,
) -> tuple[list[EnquiryListItem], CursorKey | None]:
    """One page of enquiries, newest first (received_at, id DESC).

    Status filters are projections, so rows are fetched in keyset
    batches and filtered through the shared mapping until the
    page fills or the table is exhausted: no OFFSET, and a row
    inserted between pages can neither duplicate nor skip a row.
    """
    provider_blocked = _provider_blocked(session)
    items: list[EnquiryListItem] = []
    after = cursor
    while len(items) <= limit:
        rows = list_repo.fetch_enquiry_rows(
            session, after=after, batch=_BATCH, owner_code=owner_code, q=q,
        )
        if not rows:
            break
        for row in rows:
            after = CursorKey(row.received_at, row.id)
            status = status_for_row(row, provider_blocked=provider_blocked)
            if processing_summary is not None \
                    and status.processing_summary != processing_summary:
                continue
            if attention is not None and status.attention != attention:
                continue
            items.append(EnquiryListItem(
                id=row.id,
                display_no=row.display_no,
                received_at=row.received_at,
                service_type=row.service_type,
                owner=row,
                contact=row,
                status=status,
            ))
            if len(items) > limit:
                break
        if len(rows) < _BATCH:
            break
    next_cursor = None
    if len(items) > limit:
        last = items[limit - 1]
        next_cursor = CursorKey(last.received_at, last.id)
        items = items[:limit]
    return items, next_cursor


def list_tasks(
    session: Session,
    *,
    limit: int,
    cursor: CursorKey | None,
    now: datetime,
    status: str | None = None,
    owner_code: str | None = None,
    overdue: bool = False,
) -> tuple[list[TaskListItem], CursorKey | None]:
    """One page of tasks, newest first (created_at, id DESC)."""
    rows = list_repo.fetch_task_rows(
        session, after=cursor, limit=limit + 1, status=status,
        owner_code=owner_code, overdue=overdue, now=now,
    )
    items = [TaskListItem(row=row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = CursorKey(last.created_at, last.id)
    return items, next_cursor
