"""A33: enquiry event timeline.

GET /api/v1/enquiries/{id}/events returns visible events in
event_seq ASC order with an opaque seq cursor. Visible writes are
serialised per enquiry by a transaction advisory lock, proven here
with a two-transaction barrier on real PostgreSQL.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import crm_repo, event_repo
from app.services.read_model import encode_event_cursor
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm

WORKER = uuid.UUID("12121212-1212-1212-1212-121212121212")
ALEX: dict = {
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


def _submit(api_client: TestClient, **overrides) -> dict:
    response = api_client.post(
        "/api/v1/intake/form",
        json={**ALEX, **overrides},
        headers={
            "Idempotency-Key": str(uuid.uuid4()),
            "X-CSRF-Token": api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _events(api_client: TestClient, enquiry_id, query: str = ""):
    return api_client.get(f"/api/v1/enquiries/{enquiry_id}/events{query}")


def _sql(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return result.all() if result.returns_rows else []
    finally:
        engine.dispose()


def _drain(postgres_db: str) -> None:
    clock = FakeClock(datetime.now(timezone.utc))
    fake = FakeCrm()
    for _ in range(2):
        session = _factory(postgres_db)()
        try:
            claim = claim_next(session, WORKER, clock)
            assert claim is not None
            handle_claim(session, claim, clock, crm=fake)
        finally:
            session.close()


def test_timeline_requires_auth(api_client: TestClient) -> None:
    receipt = _submit(api_client)
    anonymous = TestClient(api_client.app, raise_server_exceptions=False)
    response = anonymous.get(
        f"/api/v1/enquiries/{receipt['enquiry_id']}/events"
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_timeline_unknown_enquiry_404(api_client: TestClient) -> None:
    response = _events(api_client, uuid.uuid4())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_timeline_empty_for_enquiry_without_events(
    api_client: TestClient, postgres_db: str
) -> None:
    rows = _sql(
        postgres_db,
        "WITH c AS (INSERT INTO leadops.contacts (email_norm, first_name) "
        "VALUES ('empty@example.com', 'Empty') RETURNING id) "
        "INSERT INTO leadops.enquiries (contact_id, required_contact_version, "
        "source, service_type, message, preferred_contact, rules_version, "
        "rules_hash, received_at) SELECT id, 1, 'web', 'bathroom', "
        "'An enquiry with no events recorded yet.', 'email', 'routing-v1', "
        ":hash, now() FROM c RETURNING id",
        {"hash": "a" * 64},
    )
    response = _events(api_client, rows[0].id)
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_timeline_order_shape_and_audit_only_excluded(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    enquiry_id = receipt["enquiry_id"]
    _drain(postgres_db)
    # Audit-only event interleaved in sequence; never visible.
    _sql(
        postgres_db,
        "INSERT INTO leadops.activity_events (enquiry_id, event_type, "
        "severity, actor_type, details_json) VALUES (:id, "
        "'job.manual_retry', 'info', 'operator', '{}'::jsonb)",
        {"id": enquiry_id},
    )
    body = _events(api_client, enquiry_id).json()
    types = [item["event_type"] for item in body["items"]]
    assert types == ["enquiry.received", "enquiry.prepared", "crm.synced"]
    assert body["next_cursor"] is None
    item = body["items"][0]
    assert set(item) == {
        "id", "event_type", "severity", "actor_type", "actor_id",
        "details", "created_at",
    }
    assert "event_seq" not in item
    assert item["details"]["source"] == "web"


def test_timeline_cursor_pagination_exactly_once(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    enquiry_id = receipt["enquiry_id"]
    _drain(postgres_db)
    page1 = _events(api_client, enquiry_id, "?limit=2").json()
    assert [i["event_type"] for i in page1["items"]] == [
        "enquiry.received", "enquiry.prepared",
    ]
    assert page1["next_cursor"] is not None
    page2 = _events(
        api_client, enquiry_id, f"?limit=2&cursor={page1['next_cursor']}"
    ).json()
    assert [i["event_type"] for i in page2["items"]] == ["crm.synced"]
    assert page2["next_cursor"] is None
    seen = [i["id"] for i in page1["items"] + page2["items"]]
    assert len(seen) == len(set(seen)) == 3


def test_timeline_invalid_cursor_and_limit(api_client: TestClient) -> None:
    receipt = _submit(api_client)
    eid = receipt["enquiry_id"]
    assert _events(api_client, eid, "?cursor=garbage!!").status_code == 422
    assert _events(api_client, eid, "?cursor=aGVsbG8=").status_code == 422
    assert _events(api_client, eid, "?limit=0").status_code == 422
    assert _events(api_client, eid, "?limit=101").status_code == 422
    assert _events(api_client, eid, "?limit=abc").status_code == 422


def test_timeline_commit_safety_two_transactions(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    enquiry_id = uuid.UUID(receipt["enquiry_id"])
    other = _submit(api_client, email="other@example.com",
                    message="Other enquiry for per-enquiry lock check.")
    other_id = uuid.UUID(other["enquiry_id"])

    t1_entered = threading.Event()
    t1_release = threading.Event()
    t2_entered = threading.Event()
    t2_release = threading.Event()
    errors: list[BaseException] = []

    def txn1() -> None:
        session = _factory(postgres_db)()
        try:
            with session.begin():
                event_repo.take_enquiry_event_lock(session, enquiry_id)
                crm_repo.insert_synced_event(
                    session, enquiry_id=enquiry_id, remote_id="r-1",
                    contact_version=1,
                )
                t1_entered.set()
                assert t1_release.wait(timeout=20)
        except BaseException as exc:
            errors.append(exc)
        finally:
            session.close()

    def txn2() -> None:
        session = _factory(postgres_db)()
        try:
            with session.begin():
                crm_repo.insert_synced_event(
                    session, enquiry_id=enquiry_id, remote_id="r-2",
                    contact_version=1,
                )
                t2_entered.set()
                assert t2_release.wait(timeout=20)
        except BaseException as exc:
            errors.append(exc)
        finally:
            session.close()

    thread1 = threading.Thread(target=txn1)
    thread1.start()
    assert t1_entered.wait(timeout=15)

    # The lock is per enquiry: another enquiry's writer proceeds.
    other_done = threading.Event()

    def other_txn() -> None:
        session = _factory(postgres_db)()
        try:
            with session.begin():
                crm_repo.insert_synced_event(
                    session, enquiry_id=other_id, remote_id="r-x",
                    contact_version=1,
                )
        except BaseException as exc:
            errors.append(exc)
        finally:
            session.close()
            other_done.set()

    other_thread = threading.Thread(target=other_txn)
    other_thread.start()
    other_thread.join(timeout=10)
    assert other_done.is_set(), "event lock blocked a different enquiry"

    thread2 = threading.Thread(target=txn2)
    thread2.start()
    time.sleep(0.5)
    assert not t2_entered.is_set(), "second writer did not block on the lock"

    # While txn1 is uncommitted, polling sees only the received event.
    before = _events(api_client, enquiry_id).json()
    assert [i["event_type"] for i in before["items"]] == ["enquiry.received"]

    t1_release.set()
    thread1.join(timeout=20)
    assert not thread1.is_alive()

    seq1 = _sql(
        postgres_db,
        "SELECT max(event_seq) AS seq FROM leadops.activity_events "
        "WHERE enquiry_id = :id",
        {"id": enquiry_id},
    )[0].seq
    cursor = encode_event_cursor(seq1)
    # txn2 is still uncommitted (paused after its insert): the poll
    # from the cursor sees nothing new yet.
    assert t2_entered.wait(timeout=15)
    mid = _events(api_client, enquiry_id, f"?cursor={cursor}").json()
    assert mid["items"] == []

    # Audit-only write while polling: still never visible.
    _sql(
        postgres_db,
        "INSERT INTO leadops.activity_events (enquiry_id, event_type, "
        "severity, actor_type, details_json) VALUES (:id, "
        "'job.manual_retry', 'info', 'operator', '{}'::jsonb)",
        {"id": enquiry_id},
    )
    t2_release.set()
    thread2.join(timeout=20)
    assert not thread2.is_alive()
    assert errors == [], f"transaction errors: {errors!r}"

    after = _events(api_client, enquiry_id, f"?cursor={cursor}").json()
    assert len(after["items"]) == 1
    assert after["items"][0]["details"]["remote_id"] == "r-2"
    full = _events(api_client, enquiry_id).json()
    assert "job.manual_retry" not in [i["event_type"] for i in full["items"]]
    assert [i["details"].get("remote_id") for i in full["items"]] == [
        None, "r-1", "r-2",
    ]
