"""Repository functions for enquiry drafts (no commits here).

One saved draft per enquiry (enquiry_drafts, keyed by enquiry_id)
plus an append-only edit history (draft_edits, one row per saved
draft_version). The draft version is independent of enquiries.version.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.db import event_repo, tables


def get_draft(session: Session, enquiry_id: uuid.UUID) -> Any | None:
    return session.execute(
        select(
            tables.enquiry_drafts.c.enquiry_id,
            tables.enquiry_drafts.c.subject,
            tables.enquiry_drafts.c.body,
            tables.enquiry_drafts.c.draft_version,
            tables.enquiry_drafts.c.updated_by,
            tables.enquiry_drafts.c.updated_at,
        ).where(tables.enquiry_drafts.c.enquiry_id == enquiry_id)
    ).one_or_none()


def get_draft_for_update(
    session: Session, enquiry_id: uuid.UUID
) -> Any | None:
    return session.execute(
        select(
            tables.enquiry_drafts.c.enquiry_id,
            tables.enquiry_drafts.c.subject,
            tables.enquiry_drafts.c.body,
            tables.enquiry_drafts.c.draft_version,
            tables.enquiry_drafts.c.updated_by,
            tables.enquiry_drafts.c.updated_at,
        )
        .where(tables.enquiry_drafts.c.enquiry_id == enquiry_id)
        .with_for_update()
    ).one_or_none()


def insert_draft(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    subject: str,
    body: str,
    operator_id: uuid.UUID,
    updated_at: datetime,
) -> int:
    """Insert the first saved draft (draft_version 1); return it."""
    session.execute(
        insert(tables.enquiry_drafts).values(
            enquiry_id=enquiry_id,
            subject=subject,
            body=body,
            draft_version=1,
            updated_by=operator_id,
            updated_at=updated_at,
        )
    )
    return 1


def update_draft(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    expected_draft_version: int,
    subject: str,
    body: str,
    operator_id: uuid.UUID,
    updated_at: datetime,
) -> int:
    """Conditional draft update keyed on the expected draft version."""
    result = session.execute(
        update(tables.enquiry_drafts)
        .where(
            tables.enquiry_drafts.c.enquiry_id == enquiry_id,
            tables.enquiry_drafts.c.draft_version == expected_draft_version,
        )
        .values(
            subject=subject,
            body=body,
            draft_version=tables.enquiry_drafts.c.draft_version + 1,
            updated_by=operator_id,
            updated_at=updated_at,
        )
    )
    return result.rowcount


def insert_draft_edit(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    draft_version: int,
    subject: str,
    body: str,
    operator_id: uuid.UUID,
    created_at: datetime,
) -> uuid.UUID:
    return session.execute(
        insert(tables.draft_edits)
        .values(
            enquiry_id=enquiry_id,
            draft_version=draft_version,
            subject=subject,
            body=body,
            operator_id=operator_id,
            created_at=created_at,
        )
        .returning(tables.draft_edits.c.id)
    ).scalar_one()


def insert_draft_saved_event(session, *args, **kwargs):
    event_repo.take_enquiry_event_lock(session, kwargs["enquiry_id"])
    return _insert_draft_saved_event_locked(session, *args, **kwargs)


def _insert_draft_saved_event_locked(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    operator_id: uuid.UUID,
    draft_version: int,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="draft.saved",
            severity="info",
            actor_type="operator",
            actor_id=operator_id,
            details_json={"draft_version": draft_version},
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()
