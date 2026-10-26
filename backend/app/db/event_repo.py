"""Repository for enquiry timeline reads and event-write locking.

Commit safety (A33): a timeline reader pages by event_seq, a global
identity that is allocated at insert time, before commit. Without
serialisation a transaction could allocate a high seq, commit late,
and a reader that already passed that seq would never see the
event. Every visible event writer therefore holds a
transaction-scoped advisory lock keyed by enquiry_id from before
its insert until commit, so per-enquiry event_seq allocation and
commit are serialised. The lock namespace is distinct from the
intake content/event-key/email locks.

Lock order: callers take their existing row locks first (job,
contact, enquiry, task, health rows) and the event advisory lock
last, inside the event insert functions themselves. Audit-only
event writers (app.services.read_model.AUDIT_ONLY_EVENT_TYPES) do
not take this lock and are never on a timeline.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import tables

_NAMESPACE = b"enquiry_event\x00"


def _lock_key(enquiry_id: uuid.UUID) -> int:
    digest = hashlib.sha256(
        _NAMESPACE + str(enquiry_id).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def take_enquiry_event_lock(session: Session, enquiry_id: uuid.UUID) -> None:
    """Hold the per-enquiry event lock until this transaction ends."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _lock_key(enquiry_id)},
    )


def enquiry_exists(session: Session, enquiry_id: uuid.UUID) -> bool:
    return session.execute(
        select(tables.enquiries.c.id).where(
            tables.enquiries.c.id == enquiry_id
        )
    ).scalar_one_or_none() is not None


def fetch_events(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    after_seq: int | None,
    limit: int,
    excluded_types: Any,
) -> list:
    """Visible events for one enquiry, event_seq ASC, past a cursor."""
    stmt = (
        select(
            tables.activity_events.c.id,
            tables.activity_events.c.event_seq,
            tables.activity_events.c.event_type,
            tables.activity_events.c.severity,
            tables.activity_events.c.actor_type,
            tables.activity_events.c.actor_id,
            tables.activity_events.c.details_json,
            tables.activity_events.c.created_at,
        )
        .where(tables.activity_events.c.enquiry_id == enquiry_id)
        .where(
            tables.activity_events.c.event_type.not_in(list(excluded_types))
        )
        .order_by(tables.activity_events.c.event_seq.asc())
        .limit(limit)
    )
    if after_seq is not None:
        stmt = stmt.where(tables.activity_events.c.event_seq > after_seq)
    return list(session.execute(stmt).all())
