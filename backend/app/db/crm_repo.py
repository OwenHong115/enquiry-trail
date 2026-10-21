"""Repository functions for CRM sync (no commits here)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import insert, select, text
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
