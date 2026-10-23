"""Repository functions for the intake write path (no commits here)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import tables


def take_content_lock(session: Session, lock_key: int) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key}
    )


def _namespaced_lock_key(namespace: bytes, value: bytes) -> int:
    """Signed first 64 bits of SHA256(namespace + value), big-endian."""
    digest = hashlib.sha256(namespace + value).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def take_event_key_lock(
    session: Session, source: str, event_key: uuid.UUID
) -> None:
    lock_key = _namespaced_lock_key(
        b"event_key\x00",
        source.encode("utf-8") + b"\x00" + str(event_key).encode("utf-8"),
    )
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key}
    )


def take_email_lock(session: Session, email_norm: str) -> None:
    lock_key = _namespaced_lock_key(
        b"email\x00", email_norm.encode("utf-8")
    )
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key}
    )


def get_receipt_by_key(
    session: Session, source: str, event_key: uuid.UUID
) -> Any | None:
    return session.execute(
        select(
            tables.ingestion_receipts.c.id,
            tables.ingestion_receipts.c.enquiry_id,
            tables.ingestion_receipts.c.payload_hash,
            tables.ingestion_receipts.c.deduplication,
        ).where(
            tables.ingestion_receipts.c.source == source,
            tables.ingestion_receipts.c.event_key == event_key,
        )
    ).one_or_none()


def get_canonical_anchor(
    session: Session, source: str, content_fingerprint: str
) -> Any | None:
    # The anchor is the most recent canonical receipt: duplicates
    # never move it, and a fresh canonical receipt (after a window
    # expired) starts a new chain with its own anchor.
    return session.execute(
        select(
            tables.ingestion_receipts.c.enquiry_id,
            tables.ingestion_receipts.c.received_at,
        )
        .where(
            tables.ingestion_receipts.c.source == source,
            tables.ingestion_receipts.c.content_fingerprint
            == content_fingerprint,
            tables.ingestion_receipts.c.deduplication == "new",
        )
        .order_by(tables.ingestion_receipts.c.received_at.desc())
        .limit(1)
    ).one_or_none()


def get_contact_by_email(
    session: Session, email_norm: str
) -> tuple[uuid.UUID, int] | None:
    row = session.execute(
        select(tables.contacts.c.id, tables.contacts.c.version).where(
            tables.contacts.c.email_norm == email_norm
        )
    ).one_or_none()
    return (row.id, row.version) if row is not None else None


def find_contact_by_phone(
    session: Session, phone_e164: str, exclude_id: uuid.UUID
) -> uuid.UUID | None:
    # Several contacts may share a phone; the match is the earliest
    # created one, deterministically.
    return session.execute(
        select(tables.contacts.c.id)
        .where(
            tables.contacts.c.phone_e164 == phone_e164,
            tables.contacts.c.id != exclude_id,
        )
        .order_by(tables.contacts.c.created_at, tables.contacts.c.id)
        .limit(1)
    ).scalar_one_or_none()


def find_contact_by_name(
    session: Session,
    first_name_folded: str,
    last_name_folded: str,
    exclude_id: uuid.UUID,
) -> uuid.UUID | None:
    return session.execute(
        select(tables.contacts.c.id)
        .where(
            func.lower(func.btrim(tables.contacts.c.first_name))
            == first_name_folded,
            func.lower(
                func.btrim(func.coalesce(tables.contacts.c.last_name, ""))
            )
            == last_name_folded,
            tables.contacts.c.id != exclude_id,
        )
        .order_by(tables.contacts.c.created_at, tables.contacts.c.id)
        .limit(1)
    ).scalar_one_or_none()


def get_display_no(session: Session, enquiry_id: uuid.UUID) -> int | None:
    return session.execute(
        select(tables.enquiries.c.display_no).where(
            tables.enquiries.c.id == enquiry_id
        )
    ).scalar_one_or_none()


def get_owner_id(session: Session, code: str) -> uuid.UUID | None:
    return session.execute(
        select(tables.owners.c.id).where(tables.owners.c.code == code)
    ).scalar_one_or_none()


def insert_contact(
    session: Session,
    *,
    email_norm: str,
    first_name: str,
    last_name: str | None,
    phone_e164: str | None,
) -> tuple[uuid.UUID, int, bool]:
    """Insert the contact atomically, or return the existing row.

    A single INSERT ... ON CONFLICT (email_norm) DO NOTHING: when
    it returns a row this call inserted the contact; when it
    returns none, the email already exists and the row is read
    back unchanged (its version is never bumped here).
    """
    stmt = (
        pg_insert(tables.contacts)
        .values(
            email_norm=email_norm,
            first_name=first_name,
            last_name=last_name,
            phone_e164=phone_e164,
        )
        .on_conflict_do_nothing(index_elements=["email_norm"])
        .returning(tables.contacts.c.id, tables.contacts.c.version)
    )
    row = session.execute(stmt).one_or_none()
    if row is not None:
        return row.id, row.version, True
    existing = get_contact_by_email(session, email_norm)
    if existing is None:  # pragma: no cover - conflict implies a row
        raise RuntimeError(f"contact conflict without a row: {email_norm}")
    return existing[0], existing[1], False


def insert_enquiry(
    session: Session,
    *,
    contact_id: uuid.UUID,
    required_contact_version: int,
    source: str,
    service_type: str,
    postcode: str | None,
    message: str,
    preferred_contact: str,
    owner_id: uuid.UUID,
    routing_reason: str,
    rules_version: str,
    rules_hash: str,
    requires_review: bool,
    review_reason_codes: list[str],
    received_at: datetime,
) -> uuid.UUID:
    return session.execute(
        insert(tables.enquiries)
        .values(
            contact_id=contact_id,
            required_contact_version=required_contact_version,
            source=source,
            service_type=service_type,
            postcode=postcode,
            message=message,
            preferred_contact=preferred_contact,
            owner_id=owner_id,
            routing_reason=routing_reason,
            rules_version=rules_version,
            rules_hash=rules_hash,
            requires_review=requires_review,
            review_reason_codes=review_reason_codes,
            received_at=received_at,
        )
        .returning(tables.enquiries.c.id)
    ).scalar_one()


def insert_receipt(
    session: Session,
    *,
    source: str,
    event_key: uuid.UUID,
    payload_hash: str,
    content_fingerprint: str,
    payload_json: dict[str, Any],
    enquiry_id: uuid.UUID,
    received_at: datetime,
) -> uuid.UUID:
    return session.execute(
        insert(tables.ingestion_receipts)
        .values(
            source=source,
            event_key=event_key,
            payload_hash=payload_hash,
            content_fingerprint=content_fingerprint,
            payload_json=payload_json,
            enquiry_id=enquiry_id,
            deduplication="new",
            received_at=received_at,
        )
        .returning(tables.ingestion_receipts.c.id)
    ).scalar_one()


def insert_initial_job(
    session: Session, *, enquiry_id: uuid.UUID, next_attempt_at: datetime
) -> uuid.UUID:
    return session.execute(
        insert(tables.jobs)
        .values(
            kind="prepare_enquiry",
            entity_id=enquiry_id,
            dedupe_key=f"prepare_enquiry:{enquiry_id}",
            state="pending",
            next_attempt_at=next_attempt_at,
        )
        .returning(tables.jobs.c.id)
    ).scalar_one()


def insert_received_event(
    session: Session, *, enquiry_id: uuid.UUID, source: str
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="enquiry.received",
            severity="info",
            actor_type="system",
            details_json={"source": source, "deduplication": "new"},
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def insert_duplicate_receipt(
    session: Session,
    *,
    source: str,
    event_key: uuid.UUID,
    payload_hash: str,
    content_fingerprint: str,
    payload_json: dict[str, Any],
    enquiry_id: uuid.UUID,
    received_at: datetime,
) -> uuid.UUID:
    return session.execute(
        insert(tables.ingestion_receipts)
        .values(
            source=source,
            event_key=event_key,
            payload_hash=payload_hash,
            content_fingerprint=content_fingerprint,
            payload_json=payload_json,
            enquiry_id=enquiry_id,
            deduplication="content_duplicate",
            received_at=received_at,
        )
        .returning(tables.ingestion_receipts.c.id)
    ).scalar_one()


def insert_content_duplicate_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    receipt_id: uuid.UUID,
    canonical_enquiry_id: uuid.UUID,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="intake.content_duplicate",
            severity="info",
            actor_type="system",
            details_json={
                "receipt_id": str(receipt_id),
                "canonical_enquiry_id": str(canonical_enquiry_id),
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def insert_possible_duplicate_event(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    matched_contact_id: uuid.UUID,
    match_reason: str,
) -> uuid.UUID:
    return session.execute(
        insert(tables.activity_events)
        .values(
            enquiry_id=enquiry_id,
            event_type="contact.possible_duplicate",
            severity="warning",
            actor_type="system",
            details_json={
                "matched_contact_id": str(matched_contact_id),
                "match_reason": match_reason,
            },
        )
        .returning(tables.activity_events.c.id)
    ).scalar_one()


def insert_follow_up_task(
    session: Session,
    *,
    enquiry_id: uuid.UUID,
    owner_id: uuid.UUID,
    due_at: datetime,
    title: str,
) -> uuid.UUID:
    return session.execute(
        insert(tables.follow_up_tasks)
        .values(
            enquiry_id=enquiry_id,
            kind="initial_follow_up",
            title=title,
            owner_id=owner_id,
            due_at=due_at,
        )
        .returning(tables.follow_up_tasks.c.id)
    ).scalar_one()
