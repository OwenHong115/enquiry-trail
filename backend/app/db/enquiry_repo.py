"""Repository reads for the enquiry detail view (no writes here)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import tables


def get_enquiry_detail(session: Session, enquiry_id: uuid.UUID) -> Any | None:
    owner = tables.owners.alias("detail_owner")
    return session.execute(
        select(
            tables.enquiries.c.id,
            tables.enquiries.c.display_no,
            tables.enquiries.c.service_type,
            tables.enquiries.c.postcode,
            tables.enquiries.c.message,
            tables.enquiries.c.preferred_contact,
            tables.enquiries.c.requires_review,
            tables.enquiries.c.review_reason_codes,
            tables.enquiries.c.resolution_note,
            tables.enquiries.c.routing_reason,
            tables.enquiries.c.rules_hash,
            tables.enquiries.c.received_at,
            tables.enquiries.c.version,
            tables.enquiries.c.scenario_run_id,
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
        )
        .join(
            tables.contacts,
            tables.contacts.c.id == tables.enquiries.c.contact_id,
        )
        .outerjoin(owner, owner.c.id == tables.enquiries.c.owner_id)
        .where(tables.enquiries.c.id == enquiry_id)
    ).one_or_none()


def get_crm_link(session: Session, contact_id: uuid.UUID) -> Any | None:
    return session.execute(
        select(
            tables.crm_links.c.remote_id,
            tables.crm_links.c.sync_status,
            tables.crm_links.c.synced_contact_version,
            tables.crm_links.c.last_error_code,
        ).where(
            tables.crm_links.c.contact_id == contact_id,
            tables.crm_links.c.provider == "hubspot",
        )
    ).one_or_none()


def get_follow_up_task(session: Session, enquiry_id: uuid.UUID) -> Any | None:
    owner = tables.owners.alias("task_owner")
    return session.execute(
        select(
            tables.follow_up_tasks.c.id,
            tables.follow_up_tasks.c.enquiry_id,
            tables.follow_up_tasks.c.kind,
            tables.follow_up_tasks.c.title,
            tables.follow_up_tasks.c.status,
            tables.follow_up_tasks.c.due_at,
            tables.follow_up_tasks.c.completed_at,
            tables.follow_up_tasks.c.cancelled_at,
            tables.follow_up_tasks.c.version,
            owner.c.id.label("owner_id"),
            owner.c.code.label("owner_code"),
            owner.c.display_name.label("owner_display_name"),
        )
        .join(owner, owner.c.id == tables.follow_up_tasks.c.owner_id)
        .where(tables.follow_up_tasks.c.enquiry_id == enquiry_id)
        .order_by(tables.follow_up_tasks.c.created_at)
        .limit(1)
    ).one_or_none()
