"""S3 red tests: A02-A06 identity, idempotency, content dedup.

Source contracts (transcribed by hand; plan package v3.0):
- Receipt atomicity and the content window (600 seconds, anchored
  at the first canonical receipt) come from the intake behaviour
  contract; event detail shapes come from the vocabulary contract.
- The advisory lock key is the signed first 64 bits of
  SHA256(source + NUL + content_fingerprint), per the same contract.

A03's 409 mapping belongs to the API slice: this file expects the
domain-level IdempotencyConflict only and does not fake a status
code. A02 deliberately does not assert an intake.replayed event:
the contract defines the event but never states its trigger.
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.services import intake
from app.services.intake import IdempotencyConflict
from support.fake_clock import FakeClock
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
CLOCK_START = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)

MESSAGE_ONE = (
    "I need a bathroom renovation next month. My budget is around AUD 20,000."
)
MESSAGE_TWO = (
    "Following up with more detail about the kitchen work and timing."
)


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": "alex.demo@example.com",
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": MESSAGE_ONE,
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }
    payload.update(overrides)
    return payload


def _submit(
    session: Session, payload: dict, event_key: uuid.UUID, clock: FakeClock
) -> intake.IntakeResult:
    return intake.submit_intake(
        session,
        payload,
        source=SOURCE,
        event_key=event_key,
        clock=clock,
        routing_config=ROUTING_CONFIG,
    )


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _counts(postgres_db: str) -> dict[str, int]:
    tables = (
        "ingestion_receipts",
        "contacts",
        "enquiries",
        "follow_up_tasks",
        "jobs",
        "activity_events",
    )
    return {
        table: _fetch(postgres_db, f"SELECT count(*) AS n FROM leadops.{table}")[
            0
        ].n
        for table in tables
    }


def _event_count(postgres_db: str, event_type: str) -> int:
    return _fetch(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.activity_events "
        "WHERE event_type = :event_type",
        {"event_type": event_type},
    )[0].n


def test_a02_same_key_concurrent_submits_persist_once(postgres_db: str) -> None:
    engine = create_engine(postgres_db)
    factory = sessionmaker(bind=engine)
    event_key = uuid.uuid4()
    clock = FakeClock(CLOCK_START)
    barrier = threading.Barrier(20)
    results: list[uuid.UUID] = []
    errors: list[BaseException] = []

    def worker() -> None:
        session = factory()
        try:
            barrier.wait(timeout=15)
            result = _submit(session, _payload(), event_key, clock)
            results.append(result.enquiry_id)
        except BaseException as exc:  # collected and asserted below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    engine.dispose()

    # Contract A02: identical key/body from 20 simultaneous intakes
    # persists exactly once and every caller sees the same enquiry.
    assert errors == [], f"unexpected submit errors: {errors!r}"
    assert len(results) == 20
    assert len(set(results)) == 1
    counts = _counts(postgres_db)
    assert counts["contacts"] == 1
    assert counts["enquiries"] == 1
    assert counts["ingestion_receipts"] == 1
    assert counts["follow_up_tasks"] == 1
    assert counts["jobs"] == 1
    assert _event_count(postgres_db, "enquiry.received") == 1


def test_a03_same_key_changed_payload_conflicts(postgres_db: str) -> None:
    factory = sessionmaker(bind=create_engine(postgres_db))
    event_key = uuid.uuid4()
    clock = FakeClock(CLOCK_START)

    session = factory()
    try:
        _submit(session, _payload(), event_key, clock)
    finally:
        session.close()

    receipt = _fetch(
        postgres_db,
        "SELECT payload_hash FROM leadops.ingestion_receipts",
    )[0]
    message = _fetch(postgres_db, "SELECT message FROM leadops.enquiries")[0]
    counts_before = _counts(postgres_db)

    session = factory()
    try:
        with pytest.raises(IdempotencyConflict):
            _submit(
                session, _payload(message=MESSAGE_TWO), event_key, clock
            )
    finally:
        session.close()

    # Contract A03: the conflict changes nothing — stored hash,
    # stored message, every row count, and no new activity event.
    assert (
        _fetch(postgres_db, "SELECT payload_hash FROM leadops.ingestion_receipts")[
            0
        ].payload_hash
        == receipt.payload_hash
    )
    assert (
        _fetch(postgres_db, "SELECT message FROM leadops.enquiries")[0].message
        == message.message
    )
    assert _counts(postgres_db) == counts_before


def test_a04_content_window_anchors_on_first_receipt(postgres_db: str) -> None:
    factory = sessionmaker(bind=create_engine(postgres_db))
    clock = FakeClock(CLOCK_START)

    def submit(key: uuid.UUID) -> intake.IntakeResult:
        session = factory()
        try:
            return _submit(session, _payload(), key, clock)
        finally:
            session.close()

    first = submit(uuid.uuid4())

    # T+599, inside the 600s window: identical canonical content
    # under a new key deduplicates onto the first enquiry.
    clock.advance_by(599)
    second = submit(uuid.uuid4())
    assert second.enquiry_id == first.enquiry_id
    counts = _counts(postgres_db)
    assert counts["ingestion_receipts"] == 2
    assert counts["enquiries"] == 1
    assert counts["follow_up_tasks"] == 1

    receipts = _fetch(
        postgres_db,
        "SELECT id, deduplication FROM leadops.ingestion_receipts "
        "ORDER BY received_at, id",
    )
    duplicate_receipt = [r for r in receipts if r.deduplication == "content_duplicate"]
    assert len(duplicate_receipt) == 1

    dup_events = _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = 'intake.content_duplicate'",
    )
    assert len(dup_events) == 1
    assert dup_events[0].details_json["receipt_id"] == str(
        duplicate_receipt[0].id
    )
    assert dup_events[0].details_json["canonical_enquiry_id"] == str(
        first.enquiry_id
    )
    assert _event_count(postgres_db, "enquiry.received") == 1

    # T+601, past the window measured from the FIRST receipt. An
    # implementation anchored on the most recent receipt (T+599)
    # would wrongly deduplicate this one too; the anchor is the
    # first canonical receipt, so this is a genuinely new enquiry.
    clock.advance_by(2)
    third = submit(uuid.uuid4())
    assert third.enquiry_id != first.enquiry_id
    counts = _counts(postgres_db)
    assert counts["ingestion_receipts"] == 3
    assert counts["enquiries"] == 2
    assert counts["follow_up_tasks"] == 2


def test_a04_anchor_resets_per_canonical_chain(postgres_db: str) -> None:
    factory = sessionmaker(bind=create_engine(postgres_db))
    clock = FakeClock(CLOCK_START)

    def submit(key: uuid.UUID) -> intake.IntakeResult:
        session = factory()
        try:
            return _submit(session, _payload(), key, clock)
        finally:
            session.close()

    first = submit(uuid.uuid4())

    # Past the first window: identical content starts a new
    # canonical chain with its own enquiry and anchor.
    clock.advance_by(601)
    second = submit(uuid.uuid4())
    assert second.enquiry_id != first.enquiry_id

    # 300s after the SECOND canonical receipt this deduplicates
    # onto E2. An anchor that stays on the first-ever receipt would
    # wrongly create a third enquiry here.
    clock.advance_by(300)
    third = submit(uuid.uuid4())
    assert third.enquiry_id == second.enquiry_id

    counts = _counts(postgres_db)
    assert counts["enquiries"] == 2
    assert counts["ingestion_receipts"] == 3
    receipts = _fetch(
        postgres_db,
        "SELECT deduplication FROM leadops.ingestion_receipts "
        "ORDER BY received_at, id",
    )
    assert receipts[-1].deduplication == "content_duplicate"


def test_a04_content_lock_blocks_concurrent_duplicate(postgres_db: str) -> None:
    factory = sessionmaker(bind=create_engine(postgres_db))
    clock = FakeClock(CLOCK_START)

    session = factory()
    try:
        first = _submit(session, _payload(), uuid.uuid4(), clock)
    finally:
        session.close()

    fingerprint = _fetch(
        postgres_db,
        "SELECT content_fingerprint FROM leadops.ingestion_receipts",
    )[0].content_fingerprint
    # Contract lock key: signed first 64 bits of
    # SHA256(source + NUL + fingerprint), big-endian.
    digest = hashlib.sha256(
        SOURCE.encode("utf-8") + b"\x00" + fingerprint.encode("utf-8")
    ).digest()
    lock_key = int.from_bytes(digest[:8], "big", signed=True)

    engine = create_engine(postgres_db)
    entered = threading.Event()
    done = threading.Event()
    outcome: dict[str, object] = {}

    def worker() -> None:
        thread_session = factory()
        try:
            entered.set()
            outcome["result"] = _submit(
                thread_session, _payload(), uuid.uuid4(), clock
            )
        except BaseException as exc:  # collected and asserted below
            outcome["error"] = exc
        finally:
            thread_session.close()
            done.set()

    with engine.connect() as lock_conn:
        lock_txn = lock_conn.begin()
        lock_conn.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key}
        )
        thread = threading.Thread(target=worker)
        thread.start()
        assert entered.wait(timeout=15)
        time.sleep(0.5)
        # Holding the content lock must hold the second submit: it
        # may not complete (as duplicate or as new) until release.
        assert not done.is_set(), "second submit completed under the lock"
        lock_txn.rollback()
        thread.join(timeout=20)
    engine.dispose()

    assert done.is_set()
    assert "error" not in outcome, f"unexpected error: {outcome.get('error')!r}"
    result = outcome["result"]
    assert isinstance(result, intake.IntakeResult)
    assert result.enquiry_id == first.enquiry_id
    receipts = _fetch(
        postgres_db,
        "SELECT deduplication FROM leadops.ingestion_receipts",
    )
    assert sorted(r.deduplication for r in receipts) == [
        "content_duplicate",
        "new",
    ]


def test_a05_returning_customer_reuses_contact(postgres_db: str) -> None:
    factory = sessionmaker(bind=create_engine(postgres_db))
    clock = FakeClock(CLOCK_START)

    def submit(payload: dict) -> intake.IntakeResult:
        session = factory()
        try:
            return _submit(session, payload, uuid.uuid4(), clock)
        finally:
            session.close()

    submit(_payload())
    clock.advance_by(3600)
    second = submit(_payload(message=MESSAGE_TWO, service_type="kitchen"))

    # Contract A05: same normalized email is the same contact; the
    # new enquiry is separate work attached to that contact.
    counts = _counts(postgres_db)
    assert counts["contacts"] == 1
    assert counts["enquiries"] == 2
    assert counts["follow_up_tasks"] == 2
    assert counts["ingestion_receipts"] == 2

    contact = _fetch(postgres_db, "SELECT id, version FROM leadops.contacts")[0]
    enquiries = _fetch(
        postgres_db,
        "SELECT contact_id, required_contact_version FROM leadops.enquiries",
    )
    assert {row.contact_id for row in enquiries} == {contact.id}
    second_enquiry = _fetch(
        postgres_db,
        "SELECT required_contact_version FROM leadops.enquiries "
        "WHERE id = :enquiry_id",
        {"enquiry_id": second.enquiry_id},
    )[0]
    assert second_enquiry.required_contact_version == contact.version


def _assert_possible_duplicate(
    postgres_db: str, first_contact_id: uuid.UUID, second_enquiry_id: uuid.UUID
) -> None:
    events = _fetch(
        postgres_db,
        "SELECT enquiry_id, details_json FROM leadops.activity_events "
        "WHERE event_type = 'contact.possible_duplicate'",
    )
    assert len(events) == 1
    event = events[0]
    assert event.enquiry_id == second_enquiry_id
    # match_reason is a free string in the contract; its wording is
    # deliberately not pinned, only its presence.
    assert event.details_json["matched_contact_id"] == str(first_contact_id)
    assert isinstance(event.details_json["match_reason"], str)
    assert event.details_json["match_reason"] != ""


def test_a06_same_phone_distinct_emails_do_not_merge(postgres_db: str) -> None:
    factory = sessionmaker(bind=create_engine(postgres_db))
    clock = FakeClock(CLOCK_START)

    def submit(payload: dict) -> intake.IntakeResult:
        session = factory()
        try:
            return _submit(session, payload, uuid.uuid4(), clock)
        finally:
            session.close()

    submit(_payload())
    first_contact = _fetch(
        postgres_db, "SELECT id, email_norm, phone_e164 FROM leadops.contacts"
    )[0]
    second = submit(
        _payload(
            first_name="Sam",
            last_name="Lee",
            email="sam.lee@example.com",
            message=MESSAGE_TWO,
        )
    )

    # Contract A06/D02: a shared phone never merges people.
    counts = _counts(postgres_db)
    assert counts["contacts"] == 2
    assert counts["enquiries"] == 2
    assert counts["follow_up_tasks"] == 2
    _assert_possible_duplicate(postgres_db, first_contact.id, second.enquiry_id)

    contacts = _fetch(
        postgres_db, "SELECT email_norm, phone_e164 FROM leadops.contacts"
    )
    assert {row.email_norm for row in contacts} == {
        "alex.demo@example.com",
        "sam.lee@example.com",
    }
    unchanged = _fetch(
        postgres_db,
        "SELECT email_norm, phone_e164 FROM leadops.contacts WHERE id = :id",
        {"id": first_contact.id},
    )[0]
    assert unchanged.email_norm == first_contact.email_norm
    assert unchanged.phone_e164 == first_contact.phone_e164


def test_a06_same_name_distinct_emails_do_not_merge(postgres_db: str) -> None:
    factory = sessionmaker(bind=create_engine(postgres_db))
    clock = FakeClock(CLOCK_START)

    def submit(payload: dict) -> intake.IntakeResult:
        session = factory()
        try:
            return _submit(session, payload, uuid.uuid4(), clock)
        finally:
            session.close()

    submit(_payload(phone=None))
    first_contact = _fetch(
        postgres_db, "SELECT id, email_norm FROM leadops.contacts"
    )[0]
    second = submit(
        _payload(email="alex.other@example.com", phone=None, message=MESSAGE_TWO)
    )

    # Contract A06/D02: a shared name never merges people either.
    counts = _counts(postgres_db)
    assert counts["contacts"] == 2
    assert counts["enquiries"] == 2
    assert counts["follow_up_tasks"] == 2
    _assert_possible_duplicate(postgres_db, first_contact.id, second.enquiry_id)
