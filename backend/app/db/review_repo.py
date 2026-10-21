"""Repository functions for review resolution (no commits here)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.db import tables


def conditional_resolve(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    expected_version: int,
    operator_id: uuid.UUID,
    resolved_at: datetime,
) -> int:
    result = session.execute(
        update(tables.enquiries)
        .where(
            tables.enquiries.c.id == enquiry_id,
            tables.enquiries.c.version == expected_version,
        )
        .values(
            requires_review=False,
            review_resolved_at=resolved_at,
            review_resolved_by=operator_id,
            version=tables.enquiries.c.version + 1,
        )
    )
    return result.rowcount


def get_enquiry_version(
    session: Session, enquiry_id: uuid.UUID
) -> int | None:
    return session.execute(
        select(tables.enquiries.c.version).where(
            tables.enquiries.c.id == enquiry_id
        )
    ).scalar_one_or_none()


def insert_review_resolved_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    operator_id: uuid.UUID,
    resolution_note: str,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="review.resolved",
            severity="info",
            actor_type="operator",
            actor_id=operator_id,
            details_json={"resolution_note": resolution_note},
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()
