"""Repository reads for the enquiry detail view (no writes here)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.db import event_repo, tables


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


def get_sync_job_for_contact(
    session: Session, contact_id: uuid.UUID
) -> Any | None:
    """Return the one sync job shared by a contact's enquiries.

    A sync job is shared per contact, not per enquiry: its
    canonical key is the jobs.dedupe_key "sync_contact:<contact_id>"
    (see insert_sync_job in app/db/job_repo.py), whose uniqueness
    guarantees at most one row here. Resolving through the enquiry
    id would miss the shared job for every enquiry after the first.
    """
    return session.execute(
        select(
            tables.jobs.c.state,
            tables.jobs.c.next_attempt_at,
            tables.jobs.c.last_error_code,
        )
        .where(
            tables.jobs.c.dedupe_key == f"sync_contact:{contact_id}",
            tables.jobs.c.kind == "sync_contact",
        )
        .order_by(tables.jobs.c.created_at, tables.jobs.c.id)
        .limit(1)
    ).one_or_none()


def get_prepare_job(session: Session, enquiry_id: uuid.UUID) -> Any | None:
    """Return THIS enquiry's own prepare_enquiry job, if any.

    Unlike the sync job, a prepare job is per enquiry, so this
    lookup is by entity_id: its state drives the contract's
    local_prepare_dead rule in the detail builder.
    """
    return session.execute(
        select(tables.jobs.c.state)
        .where(
            tables.jobs.c.entity_id == enquiry_id,
            tables.jobs.c.kind == "prepare_enquiry",
        )
        .order_by(tables.jobs.c.created_at, tables.jobs.c.id)
        .limit(1)
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


def get_enquiry_for_update(
    session: Session, enquiry_id: uuid.UUID
) -> Any | None:
    """Lock one enquiry row (FOR UPDATE) and return owner facts.

    Callers take this row lock first; the event advisory lock is
    taken last, inside the event insert functions (see event_repo).
    """
    owner = tables.owners.alias("lock_owner")
    return session.execute(
        select(
            tables.enquiries.c.id,
            tables.enquiries.c.version,
            tables.enquiries.c.owner_id,
            owner.c.code.label("owner_code"),
        )
        .outerjoin(owner, owner.c.id == tables.enquiries.c.owner_id)
        .where(tables.enquiries.c.id == enquiry_id)
        .with_for_update(of=tables.enquiries)
    ).one_or_none()


def get_owner_by_code(session: Session, code: str) -> Any | None:
    return session.execute(
        select(
            tables.owners.c.id,
            tables.owners.c.code,
            tables.owners.c.display_name,
        ).where(tables.owners.c.code == code)
    ).one_or_none()


def update_enquiry_owner(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    expected_version: int,
    new_owner_id: uuid.UUID,
) -> int:
    """Conditional owner update keyed on the expected version."""
    result = session.execute(
        update(tables.enquiries)
        .where(
            tables.enquiries.c.id == enquiry_id,
            tables.enquiries.c.version == expected_version,
        )
        .values(
            owner_id=new_owner_id,
            version=tables.enquiries.c.version + 1,
        )
    )
    return result.rowcount


def insert_owner_changed_event(session, *args, **kwargs):
    event_repo.take_enquiry_event_lock(session, kwargs["enquiry_id"])
    return _insert_owner_changed_event_locked(session, *args, **kwargs)


def _insert_owner_changed_event_locked(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    operator_id: uuid.UUID,
    from_owner_code: str | None,
    to_owner_code: str,
    from_owner_id: uuid.UUID | None,
    to_owner_id: uuid.UUID,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="owner.changed",
            severity="info",
            actor_type="operator",
            actor_id=operator_id,
            details_json={
                "from_owner_code": from_owner_code,
                "to_owner_code": to_owner_code,
                "from_owner_id": str(from_owner_id) if from_owner_id else None,
                "to_owner_id": str(to_owner_id),
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def get_related_jobs(session: Session, enquiry_id: uuid.UUID, contact_id: uuid.UUID) -> list:
    """Own enquiry jobs plus the stable sync job shared through its contact."""
    return list(session.execute(
        select(tables.jobs.c.id, tables.jobs.c.kind, tables.jobs.c.state,
               tables.jobs.c.updated_at)
        .where((tables.jobs.c.entity_id == enquiry_id) |
               ((tables.jobs.c.kind == "sync_contact") &
                (tables.jobs.c.dedupe_key == f"sync_contact:{contact_id}")))
        .order_by(tables.jobs.c.created_at, tables.jobs.c.id)
    ).all())
