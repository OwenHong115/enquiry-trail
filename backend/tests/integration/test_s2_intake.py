"""S2 red tests: A01 and A25, persistence half only.

Source contracts (transcribed by hand; plan package v3.0):
- The Alex payload is transcribed from the intake fixture artifact.
- payload/fingerprint expectations are transcribed from the canonical
  vectors artifact (payload_sha256 / fingerprint_sha256).
- A01 fixes clock_start 2026-10-21T00:00:00Z; both received_at fields
  are BusinessClock-owned, so both must equal that instant.

A01 also requires a "detail_ready schema": that is an API response
shape and is proven at the API slice. It is deliberately not faked
here. A25's 503 mapping is likewise API-level and is not asserted
here; this file proves atomicity only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.domain.routing import routing_config_hash
from app.services import intake
from support.fake_clock import FakeClock
from support.routing_config import EXPECTED_ROUTING_SHA256, ROUTING_CONFIG

# Transcribed from the intake fixture artifact (intake_alex).
ALEX_PAYLOAD: dict = {
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": "alex.demo@example.com",
    "phone": "+61412345678",
    "postcode": "2150",
    "service_type": "bathroom",
    "message": "I need a bathroom renovation next month. My budget is around AUD 20,000.",
    "preferred_contact": "email",
    "acknowledge_demo": True,
}

# Transcribed from the canonical vectors artifact.
EXPECTED_PAYLOAD_SHA256 = (
    "d0fde3f2522a2255593ba26aa8ea7edca1fb8f5b1ef14bd02e1c2e45cf7ae597"
)
EXPECTED_FINGERPRINT_SHA256 = (
    "a49c6d7eec049017a0a5ffb077eb3c1a4af30ff7b24b99299c88613bff6020ea"
)

SOURCE = "web"
EVENT_KEY = uuid.UUID("12345678-1234-5678-1234-567812345678")
# A01 given: clock_start 2026-10-21T00:00:00Z.
CLOCK_START = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)

COUNT_TABLES = ("ingestion_receipts", "contacts", "enquiries", "follow_up_tasks")


def _counts(postgres_db: str) -> dict[str, int]:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return {
                table: conn.execute(
                    text(f"SELECT count(*) FROM leadops.{table}")
                ).scalar_one()
                for table in COUNT_TABLES
            }
    finally:
        engine.dispose()


def _submit(session: Session) -> None:
    intake.submit_intake(
        session,
        ALEX_PAYLOAD,
        source=SOURCE,
        event_key=EVENT_KEY,
        clock=FakeClock(CLOCK_START),
        routing_config=ROUTING_CONFIG,
    )


def test_routing_transcription_matches_authority_hash() -> None:
    # Pin: the shared transcription must hash to the authority value,
    # or every rules_hash assertion below would be meaningless.
    assert routing_config_hash(ROUTING_CONFIG) == EXPECTED_ROUTING_SHA256


def test_a01_submit_persists_receipt_contact_enquiry_and_task(
    db_session: Session, postgres_db: str
) -> None:
    _submit(db_session)

    # Read back with raw SQL, never through the service under test.
    assert _counts(postgres_db) == {
        "ingestion_receipts": 1,
        "contacts": 1,
        "enquiries": 1,
        "follow_up_tasks": 1,
    }

    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            contact = conn.execute(
                text(
                    "SELECT email_norm, phone_e164, version "
                    "FROM leadops.contacts"
                )
            ).one()
            enquiry = conn.execute(
                text(
                    "SELECT o.code AS owner_code, e.routing_reason, "
                    "e.rules_hash, e.required_contact_version, e.received_at "
                    "FROM leadops.enquiries e "
                    "JOIN leadops.owners o ON o.id = e.owner_id"
                )
            ).one()
            receipt = conn.execute(
                text(
                    "SELECT payload_hash, content_fingerprint, received_at "
                    "FROM leadops.ingestion_receipts"
                )
            ).one()
    finally:
        engine.dispose()

    # Contract: stored email/phone are the S1 normalized values.
    assert contact.email_norm == "alex.demo@example.com"
    assert contact.phone_e164 == "+61412345678"
    # Contract: the enquiry carries the R02 decision for 2150/bathroom.
    assert enquiry.owner_code == "maya"
    assert enquiry.routing_reason == "west_renovation"
    assert enquiry.rules_hash == EXPECTED_ROUTING_SHA256
    # Contract: required version is the contact version at commit,
    # read here as the persisted contact row's own version.
    assert enquiry.required_contact_version == contact.version
    # Contract: canonical payload and fingerprint hashes.
    assert receipt.payload_hash == EXPECTED_PAYLOAD_SHA256
    assert receipt.content_fingerprint == EXPECTED_FINGERPRINT_SHA256
    # Contract: BusinessClock owns both received_at fields (A01
    # clock_start); neither may come from wall-clock time.
    assert receipt.received_at == CLOCK_START
    assert enquiry.received_at == CLOCK_START


def test_a25_failed_task_insert_leaves_no_partial_intake(
    db_session: Session, postgres_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fault seam: fail at the barrier just before the follow-up task
    # insert. This patches behaviour, and asserts only persisted
    # facts below — never call counts.
    def _barrier(*args: object, **kwargs: object) -> None:
        raise RuntimeError("barrier: task insert forced to fail")

    monkeypatch.setattr(intake, "create_follow_up_task", _barrier)
    with pytest.raises(RuntimeError):
        _submit(db_session)
    db_session.rollback()

    # Contract: receipt, contact and enquiry are one atomic unit; a
    # failure before the task insert leaves no half intake behind.
    assert _counts(postgres_db) == {
        "ingestion_receipts": 0,
        "contacts": 0,
        "enquiries": 0,
        "follow_up_tasks": 0,
    }

    # Retry with the same key now succeeds with exactly one of each.
    # (This proves atomicity, not deduplication; same-key replay
    # semantics belong to a later slice and are not asserted here.)
    monkeypatch.undo()
    _submit(db_session)
    assert _counts(postgres_db) == {
        "ingestion_receipts": 1,
        "contacts": 1,
        "enquiries": 1,
        "follow_up_tasks": 1,
    }
