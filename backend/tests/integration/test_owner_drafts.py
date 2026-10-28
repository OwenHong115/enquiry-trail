"""Owner actions (T1, A23) and template drafts (T4, A23).

Behaviour pinned here:
- An owner change is guarded by the enquiry version: exactly one of
  two racing updates with the same expected version succeeds, the
  other conflicts, and exactly one owner.changed event is written.
- The open follow-up task owner stays consistent with the enquiry
  owner; a task in a terminal state keeps its historical owner.
- A draft has an independent draft_version: a draft save and an
  owner change on the same enquiry both succeed, the saved draft is
  persisted with its edit history and a draft.saved event, and the
  enquiry detail returns the saved draft instead of None.

Route shapes (chosen for this branch, mirroring review/resolve):
- POST /api/v1/enquiries/{id}/owner/change {owner_code, expected_version}
- GET  /api/v1/enquiries/{id}/draft
- PUT  /api/v1/enquiries/{id}/draft {subject, body, expected_draft_version}
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services import drafts, owner, tasks
from app.services.errors import EnquiryNotFound, VersionConflict
from app.services.intake import IntakeResult, submit_intake
from app.services.owner import UnknownOwner
from support.fake_clock import FakeClock
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
CLOCK_START = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
MESSAGE = (
    "I need a bathroom renovation next month. My budget is around AUD 20,000."
)
CSRF_HEADER = "X-CSRF-Token"
KEY_HEADER = "Idempotency-Key"

ALEX: dict = {
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


def _payload(**overrides: object) -> dict:
    payload = dict(ALEX)
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
        "SELECT enquiry_id, actor_type, actor_id, details_json "
        "FROM leadops.activity_events WHERE event_type = :event_type",
        {"event_type": event_type},
    )


def _enquiry_owner(postgres_db: str, enquiry_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT o.code AS owner_code, e.version FROM leadops.enquiries e "
        "JOIN leadops.owners o ON o.id = e.owner_id WHERE e.id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]


def _task_owner(postgres_db: str, enquiry_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT o.code AS owner_code, t.status, t.version "
        "FROM leadops.follow_up_tasks t "
        "JOIN leadops.owners o ON o.id = t.owner_id "
        "WHERE t.enquiry_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]


def _change_owner(
    postgres_db: str,
    enquiry_id: uuid.UUID,
    *,
    owner_code: str,
    expected_version: int,
    operator_id: object,
    clock: FakeClock,
) -> None:
    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        owner.change_owner(
            session,
            enquiry_id,
            new_owner_code=owner_code,
            expected_version=expected_version,
            operator_id=operator_id,  # type: ignore[arg-type]
            clock=clock,
        )
    finally:
        session.close()


def test_owner_change_updates_enquiry_open_task_and_event(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(postgres_db, _payload(), clock)
    enquiry_id = result.enquiry_id
    assert _enquiry_owner(postgres_db, enquiry_id).owner_code == "maya"
    assert _task_owner(postgres_db, enquiry_id).owner_code == "maya"

    _change_owner(
        postgres_db,
        enquiry_id,
        owner_code="oliver",
        expected_version=1,
        operator_id=seeded_operator,
        clock=clock,
    )

    enquiry = _enquiry_owner(postgres_db, enquiry_id)
    assert enquiry.owner_code == "oliver"
    assert enquiry.version == 2
    task = _task_owner(postgres_db, enquiry_id)
    assert task.owner_code == "oliver"
    assert task.status == "open"

    events = _events(postgres_db, "owner.changed")
    assert len(events) == 1
    assert events[0].actor_type == "operator"
    assert events[0].actor_id == seeded_operator
    assert events[0].details_json["from_owner_code"] == "maya"
    assert events[0].details_json["to_owner_code"] == "oliver"


def test_owner_change_stale_version_conflicts_and_writes_nothing(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(postgres_db, _payload(), clock)
    enquiry_id = result.enquiry_id

    with pytest.raises(VersionConflict):
        _change_owner(
            postgres_db,
            enquiry_id,
            owner_code="oliver",
            expected_version=99,
            operator_id=seeded_operator,
            clock=clock,
        )

    enquiry = _enquiry_owner(postgres_db, enquiry_id)
    assert enquiry.owner_code == "maya"
    assert enquiry.version == 1
    assert _task_owner(postgres_db, enquiry_id).owner_code == "maya"
    assert len(_events(postgres_db, "owner.changed")) == 0


def test_owner_change_race_exactly_one_succeeds(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(postgres_db, _payload(), clock)
    enquiry_id = result.enquiry_id

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def attempt(owner_code: str) -> None:
        factory = sessionmaker(bind=create_engine(postgres_db))
        session = factory()
        try:
            barrier.wait(timeout=10)
            owner.change_owner(
                session,
                enquiry_id,
                new_owner_code=owner_code,
                expected_version=1,
                operator_id=seeded_operator,  # type: ignore[arg-type]
                clock=clock,
            )
            with outcomes_lock:
                outcomes.append("ok")
        except VersionConflict:
            with outcomes_lock:
                outcomes.append("conflict")
        finally:
            session.close()

    threads = [
        threading.Thread(target=attempt, args=("oliver",)),
        threading.Thread(target=attempt, args=("intake",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes) == ["conflict", "ok"]
    enquiry = _enquiry_owner(postgres_db, enquiry_id)
    assert enquiry.version == 2
    assert enquiry.owner_code in ("oliver", "intake")
    # The open task owner stays consistent with the winning owner.
    assert _task_owner(postgres_db, enquiry_id).owner_code == enquiry.owner_code
    assert len(_events(postgres_db, "owner.changed")) == 1


def test_owner_change_unknown_owner_and_missing_enquiry(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(postgres_db, _payload(), clock)

    with pytest.raises(UnknownOwner):
        _change_owner(
            postgres_db,
            result.enquiry_id,
            owner_code="nobody",
            expected_version=1,
            operator_id=seeded_operator,
            clock=clock,
        )
    with pytest.raises(EnquiryNotFound):
        _change_owner(
            postgres_db,
            uuid.uuid4(),
            owner_code="oliver",
            expected_version=1,
            operator_id=seeded_operator,
            clock=clock,
        )
    assert len(_events(postgres_db, "owner.changed")) == 0


def test_owner_change_leaves_terminal_task_owner_unchanged(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(postgres_db, _payload(), clock)
    enquiry_id = result.enquiry_id
    task_id = _fetch(
        postgres_db,
        "SELECT id FROM leadops.follow_up_tasks WHERE enquiry_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0].id

    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        tasks.complete_task(
            session,
            task_id,
            expected_version=1,
            reason="Called the customer",
            clock=clock,
            operator_id=seeded_operator,  # type: ignore[arg-type]
        )
    finally:
        session.close()

    _change_owner(
        postgres_db,
        enquiry_id,
        owner_code="oliver",
        expected_version=1,
        operator_id=seeded_operator,
        clock=clock,
    )

    assert _enquiry_owner(postgres_db, enquiry_id).owner_code == "oliver"
    task = _task_owner(postgres_db, enquiry_id)
    assert task.status == "completed"
    assert task.owner_code == "maya"


def _save_draft(
    postgres_db: str,
    enquiry_id: uuid.UUID,
    *,
    subject: str,
    body: str,
    expected_draft_version: int | None,
    operator_id: object,
    clock: FakeClock,
):
    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        return drafts.save_draft(
            session,
            enquiry_id,
            subject=subject,
            body=body,
            expected_draft_version=expected_draft_version,
            operator_id=operator_id,  # type: ignore[arg-type]
            clock=clock,
        )
    finally:
        session.close()


def _get_draft(postgres_db: str, enquiry_id: uuid.UUID):
    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        return drafts.get_draft(session, enquiry_id)
    finally:
        session.close()


def test_draft_template_is_offered_but_not_persisted(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(postgres_db, _payload(), clock)
    enquiry_id = result.enquiry_id

    first = _get_draft(postgres_db, enquiry_id)
    second = _get_draft(postgres_db, enquiry_id)

    # The unsaved template is deterministic and clearly unsaved.
    assert first == second
    assert first.draft_version == 0
    assert "Alex" in first.body
    assert (
        _fetch(
            postgres_db,
            "SELECT enquiry_id FROM leadops.enquiry_drafts "
            "WHERE enquiry_id = :enquiry_id",
            {"enquiry_id": enquiry_id},
        )
        == []
    )


def test_draft_save_persists_history_event_and_bumps_only_draft_version(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(postgres_db, _payload(), clock)
    enquiry_id = result.enquiry_id

    saved = _save_draft(
        postgres_db,
        enquiry_id,
        subject="Your bathroom enquiry",
        body="Hi Alex, thanks for your bathroom enquiry.",
        expected_draft_version=None,
        operator_id=seeded_operator,
        clock=clock,
    )
    assert saved.draft_version == 1

    rows = _fetch(
        postgres_db,
        "SELECT subject, body, draft_version FROM leadops.enquiry_drafts "
        "WHERE enquiry_id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )
    assert len(rows) == 1
    assert rows[0].subject == "Your bathroom enquiry"
    assert rows[0].draft_version == 1

    updated = _save_draft(
        postgres_db,
        enquiry_id,
        subject="Updated subject for Alex",
        body="Hi Alex, an updated follow-up about your bathroom.",
        expected_draft_version=1,
        operator_id=seeded_operator,
        clock=clock,
    )
    assert updated.draft_version == 2

    edits = _fetch(
        postgres_db,
        "SELECT draft_version, subject FROM leadops.draft_edits "
        "WHERE enquiry_id = :enquiry_id ORDER BY draft_version",
        {"enquiry_id": enquiry_id},
    )
    assert [row.draft_version for row in edits] == [1, 2]
    assert edits[0].subject == "Your bathroom enquiry"
    assert edits[1].subject == "Updated subject for Alex"

    events = _events(postgres_db, "draft.saved")
    assert len(events) == 2
    assert events[-1].details_json["draft_version"] == 2

    # Draft saves never move the enquiry version.
    assert _enquiry_owner(postgres_db, enquiry_id).version == 1

    with pytest.raises(VersionConflict):
        _save_draft(
            postgres_db,
            enquiry_id,
            subject="Stale write should fail",
            body="This body must never be persisted anywhere.",
            expected_draft_version=1,
            operator_id=seeded_operator,
            clock=clock,
        )
    assert len(_events(postgres_db, "draft.saved")) == 2


def test_draft_save_and_owner_change_do_not_conflict(
    postgres_db: str, seeded_operator: object
) -> None:
    clock = FakeClock(CLOCK_START)
    result = _submit(postgres_db, _payload(), clock)
    enquiry_id = result.enquiry_id

    _save_draft(
        postgres_db,
        enquiry_id,
        subject="Your bathroom enquiry",
        body="Hi Alex, thanks for your bathroom enquiry.",
        expected_draft_version=None,
        operator_id=seeded_operator,
        clock=clock,
    )
    # The enquiry version is still 1, so the owner change succeeds.
    _change_owner(
        postgres_db,
        enquiry_id,
        owner_code="oliver",
        expected_version=1,
        operator_id=seeded_operator,
        clock=clock,
    )
    # And the draft version is still 1, so the next draft save succeeds.
    saved = _save_draft(
        postgres_db,
        enquiry_id,
        subject="Still your bathroom enquiry",
        body="Hi Alex, following up again on your bathroom.",
        expected_draft_version=1,
        operator_id=seeded_operator,
        clock=clock,
    )
    assert saved.draft_version == 2
    assert _enquiry_owner(postgres_db, enquiry_id).owner_code == "oliver"
    assert _enquiry_owner(postgres_db, enquiry_id).version == 2


def test_owner_change_http_roundtrip(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt_response = api_client.post(
        "/api/v1/intake/form",
        json=ALEX,
        headers={
            KEY_HEADER: str(uuid.uuid4()),
            CSRF_HEADER: api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert receipt_response.status_code == 202, receipt_response.text
    receipt = receipt_response.json()
    enquiry_id = receipt["enquiry_id"]

    detail_response = api_client.get(receipt["status_url"])
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["owner"]["code"] == "maya"
    assert detail["version"] == 1
    assert detail["draft"] is None

    changed = api_client.post(
        f"/api/v1/enquiries/{enquiry_id}/owner/change",
        json={"owner_code": "oliver", "expected_version": 1},
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["owner"]["code"] == "oliver"
    assert body["version"] == 2
    assert body["task"]["owner"]["code"] == "oliver"

    stale = api_client.post(
        f"/api/v1/enquiries/{enquiry_id}/owner/change",
        json={"owner_code": "intake", "expected_version": 1},
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "version_conflict"

    unknown = api_client.post(
        f"/api/v1/enquiries/{enquiry_id}/owner/change",
        json={"owner_code": "nobody", "expected_version": 2},
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )
    assert unknown.status_code == 422, unknown.text
    assert unknown.json()["error"]["code"] == "validation_failed"

    no_csrf = api_client.post(
        f"/api/v1/enquiries/{enquiry_id}/owner/change",
        json={"owner_code": "intake", "expected_version": 2},
    )
    assert no_csrf.status_code == 403, no_csrf.text

    assert len(_events(postgres_db, "owner.changed")) == 1


def test_draft_http_roundtrip_and_detail(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt_response = api_client.post(
        "/api/v1/intake/form",
        json=ALEX,
        headers={
            KEY_HEADER: str(uuid.uuid4()),
            CSRF_HEADER: api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert receipt_response.status_code == 202, receipt_response.text
    receipt = receipt_response.json()
    enquiry_id = receipt["enquiry_id"]

    template = api_client.get(f"/api/v1/enquiries/{enquiry_id}/draft")
    assert template.status_code == 200, template.text
    template_body = template.json()
    assert template_body["draft_version"] == 0
    assert "Alex" in template_body["body"]

    saved = api_client.put(
        f"/api/v1/enquiries/{enquiry_id}/draft",
        json={
            "subject": template_body["subject"],
            "body": template_body["body"],
            "expected_draft_version": 0,
        },
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    assert saved_body["draft_version"] == 1
    assert saved_body["subject"] == template_body["subject"]

    detail = api_client.get(receipt["status_url"]).json()
    assert detail["draft"] is not None
    assert detail["draft"]["subject"] == template_body["subject"]
    assert detail["draft"]["body"] == template_body["body"]
    assert detail["draft"]["draft_version"] == 1
    # The enquiry version did not move for a draft save.
    assert detail["version"] == 1

    stale = api_client.put(
        f"/api/v1/enquiries/{enquiry_id}/draft",
        json={
            "subject": "Stale subject that fails",
            "body": "Stale body that must not be saved.",
            "expected_draft_version": 0,
        },
        headers={CSRF_HEADER: api_client.csrf_token},  # type: ignore[attr-defined]
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "version_conflict"

    assert len(_events(postgres_db, "draft.saved")) == 1
