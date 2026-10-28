"""Repository for enrichment persistence (one row per enquiry)."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import event_repo, tables


def get_enquiry_message(session: Session, enquiry_id: uuid.UUID) -> str | None:
    return session.execute(
        select(tables.enquiries.c.message).where(
            tables.enquiries.c.id == enquiry_id
        )
    ).scalar_one_or_none()


def get_enrichment(session: Session, enquiry_id: uuid.UUID) -> Any | None:
    return session.execute(
        select(tables.enrichments).where(
            tables.enrichments.c.enquiry_id == enquiry_id
        )
    ).one_or_none()


def insert_enrichment(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    status: str,
    input_hash: str,
    provider: str | None,
    model: str | None,
    prompt_version: str,
    parameters_snapshot: dict,
    result_json: dict | None,
    last_error_code: str | None,
) -> Any:
    """Insert the enrichment row idempotently; return the stored row."""
    stmt = (
        pg_insert(tables.enrichments)
        .values(
            enquiry_id=enquiry_id,
            status=status,
            input_hash=input_hash,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            parameters_snapshot=parameters_snapshot,
            result_json=result_json,
            last_error_code=last_error_code,
        )
        .on_conflict_do_nothing(index_elements=["enquiry_id"])
    )
    session.execute(stmt)
    return get_enrichment(session, enquiry_id)


def message_input_hash(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def insert_enrichment_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    event_type: str,
    status: str,
) -> uuid.UUID:
    event_repo.take_enquiry_event_lock(session, enquiry_id)
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type=event_type,
            severity="info" if event_type == "enrichment.completed" else "warning",
            actor_type="worker",
            details_json={"status": status},
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()
