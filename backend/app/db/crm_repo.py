"""Repository functions for CRM sync (no commits here)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import tables


def get_enquiry_contact(session: Session, enquiry_id: uuid.UUID) -> Any | None:
    return session.execute(
        select(
            tables.contacts.c.id.label("contact_id"),
            tables.contacts.c.email_norm,
            tables.contacts.c.first_name,
            tables.contacts.c.last_name,
            tables.contacts.c.phone_e164,
            tables.contacts.c.version,
        )
        .join(
            tables.enquiries,
            tables.enquiries.c.contact_id == tables.contacts.c.id,
        )
        .where(tables.enquiries.c.id == enquiry_id)
    ).one_or_none()


def upsert_crm_link(
    session: Session,
    *,
    contact_id: uuid.UUID,
    remote_id: str,
    synced_contact_version: int,
) -> None:
    stmt = (
        pg_insert(tables.crm_links)
        .values(
            contact_id=contact_id,
            provider="hubspot",
            remote_id=remote_id,
            sync_status="synced",
            synced_contact_version=synced_contact_version,
            last_synced_at=text("clock_timestamp()"),
        )
        .on_conflict_do_update(
            index_elements=["contact_id", "provider"],
            set_={
                "remote_id": remote_id,
                "sync_status": "synced",
                "synced_contact_version": synced_contact_version,
                "last_synced_at": text("clock_timestamp()"),
            },
        )
    )
    session.execute(stmt)


def insert_operation_intent(
    session: Session,
    *,
    job_id: uuid.UUID,
    operation_key: str,
    request_hash: str,
) -> uuid.UUID:
    stmt = (
        pg_insert(tables.external_operations)
        .values(
            job_id=job_id,
            operation_key=operation_key,
            provider="hubspot",
            verb="create",
            request_hash=request_hash,
            status="intent",
            attempt_count=1,
        )
        .on_conflict_do_update(
            index_elements=["operation_key"],
            set_={
                "attempt_count": tables.external_operations.c.attempt_count + 1,
                "status": "intent",
            },
        )
        .returning(tables.external_operations.c.id)
    )
    return session.execute(stmt).scalar_one()


def set_operation_succeeded(
    session: Session, operation_id: uuid.UUID, remote_id: str
) -> None:
    session.execute(
        update(tables.external_operations)
        .where(tables.external_operations.c.id == operation_id)
        .values(status="succeeded", remote_id=remote_id)
    )


def set_operation_unknown(session: Session, operation_id: uuid.UUID) -> None:
    session.execute(
        update(tables.external_operations)
        .where(tables.external_operations.c.id == operation_id)
        .values(status="unknown", remote_id=None)
    )


def get_unresolved_operation_by_job(
    session: Session, job_id: uuid.UUID
) -> Any | None:
    return session.execute(
        select(
            tables.external_operations.c.id,
            tables.external_operations.c.status,
        )
        .where(
            tables.external_operations.c.job_id == job_id,
            tables.external_operations.c.status.in_(("intent", "unknown")),
        )
        .order_by(tables.external_operations.c.id)
        .limit(1)
    ).one_or_none()


def set_operation_failed(session: Session, operation_id: uuid.UUID) -> None:
    session.execute(
        update(tables.external_operations)
        .where(tables.external_operations.c.id == operation_id)
        .values(status="failed", remote_id=None)
    )


def set_operation_reconciled(
    session: Session, operation_id: uuid.UUID, remote_id: str
) -> None:
    session.execute(
        update(tables.external_operations)
        .where(tables.external_operations.c.id == operation_id)
        .values(
            status="reconciled",
            remote_id=remote_id,
            last_checked_at=text("clock_timestamp()"),
            attempt_count=tables.external_operations.c.attempt_count + 1,
        )
    )


def touch_operation_readback(session: Session, operation_id: uuid.UUID) -> None:
    session.execute(
        update(tables.external_operations)
        .where(tables.external_operations.c.id == operation_id)
        .values(
            last_checked_at=text("clock_timestamp()"),
            attempt_count=tables.external_operations.c.attempt_count + 1,
        )
    )


def insert_operation_unknown_event(
    session: Session,
    *,
    job_id: uuid.UUID,
    enquiry_id: uuid.UUID,
    operation_id: uuid.UUID,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            job_id=job_id,
            event_type="crm.operation_unknown",
            severity="warning",
            actor_type="worker",
            details_json={"operation_id": str(operation_id)},
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def insert_recovered_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    operation_id: uuid.UUID,
    remote_id: str,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="crm.recovered_existing",
            severity="info",
            actor_type="worker",
            details_json={
                "operation_id": str(operation_id),
                "remote_id": remote_id,
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def insert_synced_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    remote_id: str,
    contact_version: int,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="crm.synced",
            severity="info",
            actor_type="worker",
            details_json={
                "remote_id": remote_id,
                "contact_version": contact_version,
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def insert_field_preserved_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    property_name: str,
    local_masked: str,
    remote_masked: str,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="crm.field_preserved",
            severity="warning",
            actor_type="worker",
            details_json={
                "property": property_name,
                "local_masked": local_masked,
                "remote_masked": remote_masked,
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()
