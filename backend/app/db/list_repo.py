"""Batched repository reads for the enquiry/task lists.

One query per page (plus the single provider-health read in the
service): each enquiry row arrives with its contact, owner, CRM
link, shared sync job and own prepare job already joined, so a
page is never N+1. Keyset order is (timestamp DESC, id DESC).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import String, func, or_, select, tuple_
from sqlalchemy.orm import Session

from app.db import tables


def fetch_enquiry_rows(
    session: Session,
    *,
    after: Any | None,
    batch: int,
    owner_code: str | None = None,
    q: str | None = None,
) -> list:
    owner = tables.owners.alias("list_enquiry_owner")
    sync_job = tables.jobs.alias("list_sync_job")
    prepare_job = tables.jobs.alias("list_prepare_job")
    stmt = (
        select(
            tables.enquiries.c.id,
            tables.enquiries.c.display_no,
            tables.enquiries.c.received_at,
            tables.enquiries.c.service_type,
            tables.enquiries.c.requires_review,
            tables.enquiries.c.local_prepared_at,
            tables.enquiries.c.required_contact_version,
            tables.contacts.c.id.label("contact_id"),
            tables.contacts.c.email_norm,
            tables.contacts.c.first_name,
            tables.contacts.c.last_name,
            tables.contacts.c.phone_e164,
            tables.contacts.c.version.label("contact_version"),
            owner.c.id.label("owner_id"),
            owner.c.code.label("owner_code"),
            owner.c.display_name.label("owner_display_name"),
            tables.crm_links.c.sync_status.label("link_sync_status"),
            tables.crm_links.c.synced_contact_version,
            sync_job.c.state.label("sync_state"),
            sync_job.c.next_attempt_at.label("sync_next_attempt_at"),
            prepare_job.c.state.label("prepare_state"),
        )
        .join(
            tables.contacts,
            tables.contacts.c.id == tables.enquiries.c.contact_id,
        )
        .outerjoin(owner, owner.c.id == tables.enquiries.c.owner_id)
        .outerjoin(
            tables.crm_links,
            (tables.crm_links.c.contact_id == tables.contacts.c.id)
            & (tables.crm_links.c.provider == "hubspot"),
        )
        .outerjoin(
            sync_job,
            (sync_job.c.kind == "sync_contact")
            & (
                sync_job.c.dedupe_key
                == func.concat(
                    "sync_contact:", tables.contacts.c.id.cast(String)
                )
            ),
        )
        .outerjoin(
            prepare_job,
            (prepare_job.c.kind == "prepare_enquiry")
            & (prepare_job.c.entity_id == tables.enquiries.c.id),
        )
    )
    if after is not None:
        stmt = stmt.where(
            tuple_(
                tables.enquiries.c.received_at, tables.enquiries.c.id
            ) < tuple_(after.created_at, after.id)
        )
    if owner_code is not None:
        stmt = stmt.where(owner.c.code == owner_code)
    if q:
        like = f"%{q}%"
        conditions = [
            tables.contacts.c.email_norm.ilike(like),
            tables.contacts.c.first_name.ilike(like),
            tables.contacts.c.last_name.ilike(like),
        ]
        # Public display number: q may be ENQ-000001, ENQ-1 or the
        # bare digits 0001; all parse to the integer display_no.
        match = re.fullmatch(r"(?:ENQ-)?(\d+)", q.strip(), re.IGNORECASE)
        if match:
            conditions.append(
                tables.enquiries.c.display_no == int(match.group(1))
            )
        stmt = stmt.where(or_(*conditions))
    stmt = stmt.order_by(
        tables.enquiries.c.received_at.desc(), tables.enquiries.c.id.desc()
    ).limit(batch)
    return list(session.execute(stmt).all())


def fetch_task_rows(
    session: Session,
    *,
    after: Any | None,
    limit: int,
    status: str | None = None,
    owner_code: str | None = None,
    overdue: bool = False,
    now: datetime | None = None,
) -> list:
    owner = tables.owners.alias("list_task_owner")
    stmt = (
        select(
            tables.follow_up_tasks.c.id,
            tables.follow_up_tasks.c.kind,
            tables.follow_up_tasks.c.title,
            tables.follow_up_tasks.c.status,
            tables.follow_up_tasks.c.due_at,
            tables.follow_up_tasks.c.created_at,
            tables.follow_up_tasks.c.completed_at,
            tables.follow_up_tasks.c.cancelled_at,
            tables.follow_up_tasks.c.version,
            owner.c.id.label("owner_id"),
            owner.c.code.label("owner_code"),
            owner.c.display_name.label("owner_display_name"),
            tables.enquiries.c.id.label("enquiry_id"),
            tables.enquiries.c.display_no.label("enquiry_display_no"),
            tables.enquiries.c.service_type.label("enquiry_service_type"),
            tables.contacts.c.id.label("contact_id"),
            tables.contacts.c.email_norm,
            tables.contacts.c.first_name,
            tables.contacts.c.last_name,
            tables.contacts.c.phone_e164,
        )
        .join(owner, owner.c.id == tables.follow_up_tasks.c.owner_id)
        .join(
            tables.enquiries,
            tables.enquiries.c.id == tables.follow_up_tasks.c.enquiry_id,
        )
        .join(
            tables.contacts,
            tables.contacts.c.id == tables.enquiries.c.contact_id,
        )
    )
    if after is not None:
        stmt = stmt.where(
            tuple_(
                tables.follow_up_tasks.c.created_at,
                tables.follow_up_tasks.c.id,
            ) < tuple_(after.created_at, after.id)
        )
    if status is not None:
        stmt = stmt.where(tables.follow_up_tasks.c.status == status)
    if owner_code is not None:
        stmt = stmt.where(owner.c.code == owner_code)
    if overdue and now is not None:
        stmt = stmt.where(
            tables.follow_up_tasks.c.status == "open",
            tables.follow_up_tasks.c.due_at < now,
        )
    stmt = stmt.order_by(
        tables.follow_up_tasks.c.created_at.desc(),
        tables.follow_up_tasks.c.id.desc(),
    ).limit(limit)
    return list(session.execute(stmt).all())
