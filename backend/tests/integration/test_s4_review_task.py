"""S4a red tests: A08 review resolution and A24 task terminal actions.

Source contracts (transcribed by hand; plan package v3.0):
- Review/owner optimistic locking uses enquiries.version; the
  repeat-terminal rule ("same target returns the original result
  before version check; other terminal target conflicts") comes
  from the behaviour contract's optimistic_locking section.
- Event shapes (review.resolved / task.completed / task.cancelled)
  come from the vocabulary contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services import review, tasks
from app.services.intake import IntakeResult, submit_intake
from app.services.review import VersionConflict
from app.services.tasks import TerminalConflict
from support.fake_clock import FakeClock
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
CLOCK_START = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
MESSAGE = (
    "I need a bathroom renovation next month. My budget is around AUD 20,000."
)


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": "alex.demo@example.com",
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": MESSAGE,
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }
    payload.update(overrides)
    return payload


def _submit(postgres_db: str, payload: dict, clock: FakeClock) -> IntakeResult:
    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        return submit_intake(
            session,
            payload,
            source=SOURCE,
            event_key=uuid.uuid4(),
            clock=clock,
            routing_config=ROUTING_CONFIG,
        )
    finally:
        session.close()


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _events(postgres_db: str, event_type: str) -> list:
    return _fetch(
        postgres_db,
        "SELECT enquiry_id, actor_type, details_json FROM leadops.activity_events "
        "WHERE event_type = :event_type",
        {"event_type": event_type},
    )


def _task_id(postgres_db: str, enquiry_id: uuid.UUID) -> uuid.UUID:
    return _fetch(
        postgres_db,
        "SELECT id FROM leadops.follow_up_tasks WHERE enquiry_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0].id


def _task_row(postgres_db: str, task_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT status, completed_at, cancelled_at, version "
        "FROM leadops.follow_up_tasks WHERE id = :task_id",
        {"task_id": task_id},
    )[0]


def test_a08_review_resolution(postgres_db: str, seeded_operator: object) -> None:
    clock = FakeClock(CLOCK_START)
    # R01: null postcode routes to intake with review.
    result = _submit(postgres_db, _payload(postcode=None), clock)
    enquiry_id = result.enquiry_id

    enquiry = _fetch(
        postgres_db,
        "SELECT o.code AS owner_code, e.requires_review, "
        "e.review_reason_codes, e.routing_reason, e.version "
        "FROM leadops.enquiries e JOIN leadops.owners o ON o.id = e.owner_id "
        "WHERE e.id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]
    # Contract A08/R01: intake owner, review required, reasons kept.
    assert enquiry.owner_code == "intake"
    assert enquiry.requires_review is True
    assert enquiry.review_reason_codes == ["missing_postcode"]
    assert enquiry.routing_reason == "missing_routing_information"
    task_owner = _fetch(
        postgres_db,
        "SELECT o.code AS owner_code FROM leadops.follow_up_tasks t "
        "JOIN leadops.owners o ON o.id = t.owner_id "
        "WHERE t.enquiry_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]
    assert task_owner.owner_code == "intake"

    clock.advance_by(3600)
    resolved_at = clock.now_utc()
    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        review.resolve_review(
            session,
            enquiry_id,
            expected_version=1,
            operator_id=seeded_operator,  # type: ignore[arg-type]
            resolution_note="Confirmed scope by phone",
            clock=clock,
        )
    finally:
        session.close()

    resolved = _fetch(
        postgres_db,
        "SELECT o.code AS owner_code, e.requires_review, "
        "e.review_reason_codes, e.review_resolved_at, "
        "e.review_resolved_by, e.version FROM leadops.enquiries e "
        "JOIN leadops.owners o ON o.id = e.owner_id WHERE e.id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]
    assert resolved.requires_review is False
    assert resolved.review_resolved_at == resolved_at
    assert resolved.review_resolved_by == seeded_operator
    assert resolved.version == 2
    # Reason history is retained and resolution never moves the owner.
    assert resolved.review_reason_codes == ["missing_postcode"]
    assert resolved.owner_code == "intake"

    events = _events(postgres_db, "review.resolved")
    assert len(events) == 1
    assert events[0].actor_type == "operator"
    assert events[0].details_json["resolution_note"] == "Confirmed scope by phone"

    # Stale version conflicts and changes nothing.
    session = factory()
    try:
        with pytest.raises(VersionConflict):
            review.resolve_review(
                session,
                enquiry_id,
                expected_version=1,
                operator_id=seeded_operator,  # type: ignore[arg-type]
                resolution_note="Second attempt",
                clock=clock,
            )
    finally:
        session.close()
    after = _fetch(
        postgres_db,
        "SELECT version, review_resolved_at FROM leadops.enquiries "
        "WHERE id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]
    assert after.version == 2
    assert after.review_resolved_at == resolved_at
    assert len(_events(postgres_db, "review.resolved")) == 1


def test_a24_complete_is_terminal_and_repeatable(postgres_db: str) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(postgres_db, _payload(), clock)
    task_id = _task_id(postgres_db, result.enquiry_id)

    clock.advance_by(600)
    completed_at = clock.now_utc()
    factory = sessionmaker(bind=create_engine(postgres_db))

    def call(fn, **kwargs: object) -> None:
        session = factory()
        try:
            fn(session, task_id, clock=clock, **kwargs)
        finally:
            session.close()

    call(tasks.complete_task, expected_version=1, reason="Called the customer")
    row = _task_row(postgres_db, task_id)
    # Contract A24: completion stamps the BusinessClock instant and
    # bumps the task version exactly once.
    assert row.status == "completed"
    assert row.completed_at == completed_at
    assert row.version == 2
    events = _events(postgres_db, "task.completed")
    assert len(events) == 1
    assert events[0].details_json["task_id"] == str(task_id)
    assert events[0].details_json["reason"] == "Called the customer"

    # Repeat of the same terminal action returns the original result
    # before any version check: stale version, no exception, no new
    # event, original timestamp retained.
    call(tasks.complete_task, expected_version=1, reason="Called the customer")
    row = _task_row(postgres_db, task_id)
    assert row.status == "completed"
    assert row.completed_at == completed_at
    assert row.version == 2
    assert len(_events(postgres_db, "task.completed")) == 1

    # The opposite terminal action conflicts and changes nothing.
    with pytest.raises(TerminalConflict):
        call(tasks.cancel_task, expected_version=2, reason="No longer needed")
    row = _task_row(postgres_db, task_id)
    assert row.status == "completed"
    assert row.completed_at == completed_at
    assert row.version == 2
    assert len(_events(postgres_db, "task.cancelled")) == 0


def test_a24_cancel_is_terminal_and_repeatable(postgres_db: str) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(
        postgres_db,
        _payload(email="sam.lee@example.com", message="Kitchen quote please, ideally this quarter."),
        clock,
    )
    task_id = _task_id(postgres_db, result.enquiry_id)

    clock.advance_by(600)
    cancelled_at = clock.now_utc()
    factory = sessionmaker(bind=create_engine(postgres_db))

    def call(fn, **kwargs: object) -> None:
        session = factory()
        try:
            fn(session, task_id, clock=clock, **kwargs)
        finally:
            session.close()

    call(tasks.cancel_task, expected_version=1, reason="Customer withdrew")
    row = _task_row(postgres_db, task_id)
    assert row.status == "cancelled"
    assert row.cancelled_at == cancelled_at
    assert row.version == 2
    events = _events(postgres_db, "task.cancelled")
    assert len(events) == 1
    assert events[0].details_json["task_id"] == str(task_id)
    assert events[0].details_json["reason"] == "Customer withdrew"

    # Repeat cancel with a stale version returns the original result.
    call(tasks.cancel_task, expected_version=1, reason="Customer withdrew")
    row = _task_row(postgres_db, task_id)
    assert row.status == "cancelled"
    assert row.cancelled_at == cancelled_at
    assert row.version == 2
    assert len(_events(postgres_db, "task.cancelled")) == 1

    # Completing a cancelled task conflicts and changes nothing.
    with pytest.raises(TerminalConflict):
        call(tasks.complete_task, expected_version=2, reason="Called the customer")
    row = _task_row(postgres_db, task_id)
    assert row.status == "cancelled"
    assert row.cancelled_at == cancelled_at
    assert row.version == 2
    assert len(_events(postgres_db, "task.completed")) == 0
