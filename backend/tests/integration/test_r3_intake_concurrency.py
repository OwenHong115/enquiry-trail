"""R3 red tests: intake serialisation beyond the content fingerprint.

Intake is safe only when concurrent requests happen to share a
content fingerprint: the sole advisory lock is keyed on
(source, content_fingerprint). Two races follow:

- Same contact email, different content: different fingerprints
  take different locks, so both intakes can observe no contact and
  both insert one; the loser hits the contacts.email_norm UNIQUE
  constraint instead of reusing the winner's contact row.
- Same (source, event_key), different payload: different payloads
  take different locks, so both can miss the receipt lookup and
  both insert a receipt; the loser hits the
  ingestion_receipts UNIQUE(source, event_key) constraint instead
  of raising IdempotencyConflict (which the API maps to 409).

No sleeps: threads coordinate only with a barrier and joins.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.services import intake
from app.services.intake import IdempotencyConflict
from support.fake_clock import FakeClock
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
CLOCK_START = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
SERVICE_TYPES = ("bathroom", "kitchen", "general")


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": "alex.demo@example.com",
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": (
            "I need a bathroom renovation next month. "
            "My budget is around AUD 20,000."
        ),
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


def _run_concurrent(
    postgres_db: str, submissions: list[tuple[dict, uuid.UUID]]
) -> tuple[list[intake.IntakeResult], list[BaseException]]:
    engine = create_engine(postgres_db)
    factory = sessionmaker(bind=engine)
    clock = FakeClock(CLOCK_START)
    barrier = threading.Barrier(len(submissions))
    results: list[intake.IntakeResult] = []
    errors: list[BaseException] = []

    def worker(payload: dict, event_key: uuid.UUID) -> None:
        session = factory()
        try:
            barrier.wait(timeout=15)
            results.append(_submit(session, payload, event_key, clock))
        except BaseException as exc:  # collected and asserted below
            errors.append(exc)
        finally:
            session.close()

    threads = [
        threading.Thread(target=worker, args=(payload, event_key))
        for payload, event_key in submissions
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    engine.dispose()
    assert not any(thread.is_alive() for thread in threads)
    return results, errors


def test_same_email_different_content_concurrent_all_succeed(
    postgres_db: str,
) -> None:
    # One email, 20 distinct contents (different message and
    # service_type, hence 20 different content fingerprints) under
    # 20 different event keys, all released by one barrier.
    submissions = [
        (
            _payload(
                message=(
                    f"Renovation enquiry variant {i}: please quote the "
                    f"work described in message number {i}."
                ),
                service_type=SERVICE_TYPES[i % len(SERVICE_TYPES)],
            ),
            uuid.uuid4(),
        )
        for i in range(20)
    ]

    results, errors = _run_concurrent(postgres_db, submissions)

    # Every submission is a distinct enquiry for the same person:
    # all must succeed by sharing the one contact row, not by one
    # of them dying on the email_norm UNIQUE constraint.
    assert errors == [], f"unexpected submit errors: {errors!r}"
    assert len(results) == 20
    assert len({result.enquiry_id for result in results}) == 20
    assert all(result.deduplication == "new" for result in results)
    assert all(not result.replayed for result in results)

    counts = _counts(postgres_db)
    assert counts["contacts"] == 1
    assert counts["enquiries"] == 20
    assert counts["ingestion_receipts"] == 20
    assert counts["follow_up_tasks"] == 20
    assert counts["jobs"] == 20
    assert _event_count(postgres_db, "enquiry.received") == 20

    contact = _fetch(postgres_db, "SELECT id, version FROM leadops.contacts")[0]
    enquiries = _fetch(
        postgres_db,
        "SELECT contact_id, required_contact_version FROM leadops.enquiries",
    )
    assert len(enquiries) == 20
    assert {row.contact_id for row in enquiries} == {contact.id}
    assert {row.required_contact_version for row in enquiries} == {
        contact.version
    }


def test_same_event_key_different_payload_concurrent_conflicts(
    postgres_db: str,
) -> None:
    # One (source, event_key), 20 different payloads, all released
    # by one barrier. Distinct emails keep the contact race out of
    # this test so only the receipt race is exercised: whichever
    # payload commits first wins, and every other payload differs
    # from it, so every loser must see IdempotencyConflict.
    event_key = uuid.uuid4()
    submissions = [
        (
            _payload(
                email=f"r3.user{i}@example.com",
                message=(
                    f"Competing payload variant {i} submitted under "
                    f"the shared event key, message number {i}."
                ),
                service_type=SERVICE_TYPES[i % len(SERVICE_TYPES)],
            ),
            event_key,
        )
        for i in range(20)
    ]

    results, errors = _run_concurrent(postgres_db, submissions)

    # Exactly one payload wins the key. The other nineteen must be
    # domain conflicts, never a raw IntegrityError from the
    # ingestion_receipts UNIQUE(source, event_key) constraint.
    assert len(results) == 1, f"unexpected results: {results!r}"
    assert len(errors) == 19, f"unexpected submit errors: {errors!r}"
    assert all(isinstance(exc, IdempotencyConflict) for exc in errors), (
        f"unexpected submit errors: {errors!r}"
    )

    counts = _counts(postgres_db)
    assert counts["contacts"] == 1
    assert counts["enquiries"] == 1
    assert counts["ingestion_receipts"] == 1
    assert counts["follow_up_tasks"] == 1
    assert counts["jobs"] == 1
    assert _event_count(postgres_db, "enquiry.received") == 1

    receipt = _fetch(
        postgres_db,
        "SELECT event_key, enquiry_id FROM leadops.ingestion_receipts",
    )[0]
    assert receipt.event_key == event_key
    assert receipt.enquiry_id == results[0].enquiry_id
