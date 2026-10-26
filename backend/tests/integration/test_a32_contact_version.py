"""A32: contact-version coordination.

Same email with new valid phone/name updates the contact and advances
its version; the stable per-contact sync_contact job is rearmed for the
new version without downgrading an already-satisfied older enquiry;
unknown operations stay readback-first; a version change mid-sync is
picked up by the same job with no duplicate CRM create.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import ProviderError
from app.services import crm_sync, intake
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
MSG1 = "I need a bathroom renovation next month. My budget is around AUD 20,000."
MSG2 = "Following up with more detail about the kitchen work and timing."
MSG3 = "A third enquiry with an updated message about the same project."


def _payload(**overrides: object) -> dict:
    payload: dict = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": "alex.demo@example.com",
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": MSG1,
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }
    payload.update(overrides)
    return payload


def _v2_payload() -> dict:
    return _payload(
        first_name="Alicia", last_name="Morgans", phone="+61487654321",
        message=MSG2, service_type="kitchen",
    )


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _submit(postgres_db: str, payload: dict, clock: FakeClock):
    session = _factory(postgres_db)()
    try:
        return intake.submit_intake(
            session, payload, source=SOURCE, event_key=uuid.uuid4(),
            clock=clock, routing_config=ROUTING_CONFIG,
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


def _contact(postgres_db: str):
    return _fetch(
        postgres_db,
        "SELECT id, first_name, last_name, phone_e164, version "
        "FROM leadops.contacts",
    )[0]


def _run_next(postgres_db: str, clock: FakeClock, crm, kind: str):
    session = _factory(postgres_db)()
    try:
        claim = claim_next(session, WORKER, clock)
    finally:
        session.close()
    assert claim is not None, f"expected a claimable {kind} job"
    assert claim.kind == kind, f"expected {kind}, got {claim.kind}"
    session = _factory(postgres_db)()
    try:
        handle_claim(session, claim, clock, crm=crm)
    finally:
        session.close()
    return claim


def _detail(api_client: TestClient, enquiry_id) -> dict:
    response = api_client.get(f"/api/v1/enquiries/{enquiry_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _sync_job(postgres_db: str):
    rows = _fetch(
        postgres_db,
        "SELECT id, entity_id, state, dedupe_key FROM leadops.jobs "
        "WHERE kind = 'sync_contact'",
    )
    assert len(rows) == 1
    return rows[0]


def _assert_remote_v2(fake: FakeCrm) -> None:
    remote = fake.remote_by_email["alex.demo@example.com"]
    assert remote.firstname == "Alicia"
    assert remote.lastname == "Morgans"
    assert remote.phone == "+61487654321"
    assert len(fake.calls_of("patch_contact")) == 1
    patch = fake.calls_of("patch_contact")[0]
    assert patch.fields["firstname"] == "Alicia"
    assert patch.fields["lastname"] == "Morgans"
    assert patch.fields["phone"] == "+61487654321"


def _link(postgres_db: str, contact_id):
    return _fetch(
        postgres_db,
        "SELECT remote_id, sync_status, synced_contact_version "
        "FROM leadops.crm_links WHERE contact_id = :cid",
        {"cid": contact_id},
    )[0]


def test_a32_same_email_new_phone_name_updates_contact_and_version(
    postgres_db: str,
) -> None:
    clock = FakeClock(T0)
    first = _submit(postgres_db, _payload(), clock)
    clock.advance_by(3600)
    second = _submit(postgres_db, _v2_payload(), clock)

    contacts = _fetch(postgres_db, "SELECT count(*) AS n FROM leadops.contacts")
    assert contacts[0].n == 1
    contact = _contact(postgres_db)
    assert contact.first_name == "Alicia"
    assert contact.phone_e164 == "+61487654321"
    assert contact.version == 2

    enquiries = {
        row.id: row.required_contact_version
        for row in _fetch(
            postgres_db,
            "SELECT id, required_contact_version FROM leadops.enquiries",
        )
    }
    assert enquiries[first.enquiry_id] == 1
    assert enquiries[second.enquiry_id] == 2

    # Identical contact data on a later enquiry does not advance it.
    clock.advance_by(3600)
    third = _submit(
        postgres_db, _payload(
            first_name="Alicia", last_name="Morgans",
            phone="+61487654321", message=MSG3,
            service_type="kitchen",
        ), clock,
    )
    assert _contact(postgres_db).version == 2
    third_req = _fetch(
        postgres_db,
        "SELECT required_contact_version FROM leadops.enquiries WHERE id = :id",
        {"id": third.enquiry_id},
    )[0]
    assert third_req.required_contact_version == 2


def test_a32_rearmed_sync_job_and_older_enquiry_stays_ready(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(T0)
    fake = FakeCrm()
    first = _submit(postgres_db, _payload(), clock)
    _run_next(postgres_db, clock, fake, "prepare_enquiry")
    _run_next(postgres_db, clock, fake, "sync_contact")
    job_before = _sync_job(postgres_db)
    assert job_before.state == "succeeded"
    assert _detail(api_client, first.enquiry_id)["processing_summary"] == "ready"

    clock.advance_by(3600)
    second = _submit(postgres_db, _v2_payload(), clock)
    _run_next(postgres_db, clock, fake, "prepare_enquiry")

    # The stable job is rearmed in place for v2, not duplicated.
    job_after = _sync_job(postgres_db)
    assert job_after.id == job_before.id
    assert job_after.state == "pending"

    older = _detail(api_client, first.enquiry_id)
    assert older["processing_summary"] == "ready"
    assert older["required_contact_version"] == 1
    assert older["crm"]["synced_contact_version"] == 1

    newer = _detail(api_client, second.enquiry_id)
    assert newer["processing_summary"] != "ready"
    assert newer["required_contact_version"] == 2
    assert newer["contact"]["version"] == 2
    assert newer["contact"]["first_name"] == "Alicia"
    assert newer["contact"]["phone_e164"] == "+61487654321"

    _run_next(postgres_db, clock, fake, "sync_contact")
    assert len(fake.calls_of("create_contact")) == 1
    assert fake.remote_contact_count == 1
    _assert_remote_v2(fake)
    contact = _contact(postgres_db)
    assert _link(postgres_db, contact.id).synced_contact_version == 2
    assert _sync_job(postgres_db).state == "succeeded"
    assert _detail(api_client, first.enquiry_id)["processing_summary"] == "ready"
    assert _detail(api_client, second.enquiry_id)["processing_summary"] == "ready"


def test_a32_unknown_operation_remains_readback_first_after_update(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(T0)
    fake = FakeCrm(
        create_commit_then_error=ProviderError("result_unknown")
    )
    first = _submit(postgres_db, _payload(), clock)
    _run_next(postgres_db, clock, fake, "prepare_enquiry")
    _run_next(postgres_db, clock, fake, "sync_contact")

    assert _sync_job(postgres_db).state == "reconciling"
    ops = _fetch(
        postgres_db, "SELECT status FROM leadops.external_operations"
    )
    assert [op.status for op in ops] == ["unknown"]
    assert len(fake.calls_of("create_contact")) == 1

    second = _submit(postgres_db, _v2_payload(), clock)
    assert _contact(postgres_db).version == 2
    clock.advance_by(3600)
    _run_next(postgres_db, clock, fake, "prepare_enquiry")
    # Rearm must not bypass the unresolved operation.
    assert _sync_job(postgres_db).state == "reconciling"
    _run_next(postgres_db, clock, fake, "sync_contact")

    assert len(fake.calls_of("create_contact")) == 1
    assert fake.remote_contact_count == 1
    _assert_remote_v2(fake)
    ops = _fetch(
        postgres_db, "SELECT status FROM leadops.external_operations"
    )
    assert [op.status for op in ops] == ["reconciled"]
    contact = _contact(postgres_db)
    assert _link(postgres_db, contact.id).synced_contact_version == 2
    assert _sync_job(postgres_db).state == "succeeded"
    assert _detail(api_client, first.enquiry_id)["processing_summary"] == "ready"
    assert _detail(api_client, second.enquiry_id)["processing_summary"] == "ready"


def test_a32_mid_sync_version_change_picked_up_without_duplicate_create(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(T0)
    first = _submit(postgres_db, _payload(), clock)
    _run_next(postgres_db, clock, FakeCrm(), "prepare_enquiry")

    session = _factory(postgres_db)()
    try:
        claim = claim_next(session, WORKER, clock)
    finally:
        session.close()
    assert claim is not None and claim.kind == "sync_contact"

    entered = threading.Event()
    release = threading.Event()
    fake = FakeCrm()

    def barrier() -> None:
        entered.set()
        assert release.wait(timeout=15)

    fake.before_create = barrier
    errors: list[BaseException] = []

    def run_claim() -> None:
        thread_session = _factory(postgres_db)()
        try:
            handle_claim(thread_session, claim, clock, crm=fake)
        except BaseException as exc:  # collected and asserted below
            errors.append(exc)
        finally:
            thread_session.close()

    thread = threading.Thread(target=run_claim)
    thread.start()
    assert entered.wait(timeout=15)
    # Contact changes while the stable job is claimed and running.
    second = _submit(postgres_db, _v2_payload(), clock)
    assert _contact(postgres_db).version == 2
    release.set()
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert errors == [], f"worker errors: {errors!r}"

    # The v1 sync recorded v1 only, and the same job is rearmed.
    contact = _contact(postgres_db)
    assert _link(postgres_db, contact.id).synced_contact_version == 1
    assert _sync_job(postgres_db).state == "pending"
    assert _detail(api_client, first.enquiry_id)["processing_summary"] == "ready"

    fake.before_create = None
    # The rearmed sync job predates the new prepare job, so it is
    # claimed first and syncs v2; the prepare then leaves the
    # already-satisfied stable job succeeded.
    _run_next(postgres_db, clock, fake, "sync_contact")
    _run_next(postgres_db, clock, fake, "prepare_enquiry")
    assert len(fake.calls_of("create_contact")) == 1
    assert fake.remote_contact_count == 1
    _assert_remote_v2(fake)
    assert _link(postgres_db, contact.id).synced_contact_version == 2
    assert _sync_job(postgres_db).state == "succeeded"
    assert _detail(api_client, first.enquiry_id)["processing_summary"] == "ready"
    assert _detail(api_client, second.enquiry_id)["processing_summary"] == "ready"


def test_a32_changed_contact_data_is_new_enquiry_and_patches_remote(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(T0)
    fake = FakeCrm()
    first = _submit(postgres_db, _payload(), clock)
    _run_next(postgres_db, clock, fake, "prepare_enquiry")
    _run_next(postgres_db, clock, fake, "sync_contact")
    assert _sync_job(postgres_db).state == "succeeded"
    assert _detail(api_client, first.enquiry_id)["processing_summary"] == "ready"

    # Same email/message within 600s, but new valid name/phone:
    # strict canonical dedup (fingerprint unchanged) makes this a
    # NEW enquiry, not a content duplicate.
    clock.advance_by(300)
    second = _submit(
        postgres_db,
        _payload(first_name="Alicia", last_name="Morgans",
                 phone="+61487654321"),
        clock,
    )
    assert second.enquiry_id != first.enquiry_id
    assert second.deduplication == "new"
    enquiries = _fetch(postgres_db, "SELECT count(*) AS n FROM leadops.enquiries")
    assert enquiries[0].n == 2
    contact = _contact(postgres_db)
    assert contact.first_name == "Alicia"
    assert contact.last_name == "Morgans"
    assert contact.phone_e164 == "+61487654321"
    assert contact.version == 2
    second_req = _fetch(
        postgres_db,
        "SELECT required_contact_version FROM leadops.enquiries WHERE id = :id",
        {"id": second.enquiry_id},
    )[0]
    assert second_req.required_contact_version == 2

    _run_next(postgres_db, clock, fake, "prepare_enquiry")
    # The stable job is rearmed for v2; the old enquiry stays ready.
    assert _sync_job(postgres_db).state == "pending"
    assert _detail(api_client, first.enquiry_id)["processing_summary"] == "ready"
    assert _detail(api_client, second.enquiry_id)["processing_summary"] != "ready"

    _run_next(postgres_db, clock, fake, "sync_contact")
    assert len(fake.calls_of("create_contact")) == 1
    _assert_remote_v2(fake)
    assert _link(postgres_db, contact.id).synced_contact_version == 2
    assert _detail(api_client, first.enquiry_id)["processing_summary"] == "ready"
    assert _detail(api_client, second.enquiry_id)["processing_summary"] == "ready"


def test_a32_unsupported_patch_does_not_advance_link(
    api_client: TestClient, postgres_db: str
) -> None:
    clock = FakeClock(T0)
    fake = FakeCrm()
    first = _submit(postgres_db, _payload(), clock)
    _run_next(postgres_db, clock, fake, "prepare_enquiry")
    _run_next(postgres_db, clock, fake, "sync_contact")
    contact = _contact(postgres_db)
    assert _link(postgres_db, contact.id).synced_contact_version == 1

    clock.advance_by(3600)
    second = _submit(postgres_db, _v2_payload(), clock)
    _run_next(postgres_db, clock, fake, "prepare_enquiry")
    fake.patch_supported = False
    _run_next(postgres_db, clock, fake, "sync_contact")

    assert fake.remote_by_email["alex.demo@example.com"].firstname == "Alex"
    assert _link(postgres_db, contact.id).synced_contact_version == 1
    assert _sync_job(postgres_db).state != "succeeded"
    detail = _detail(api_client, second.enquiry_id)
    assert detail["processing_summary"] != "ready"
    assert detail["crm"]["synced_contact_version"] == 1


def test_a32_finalization_serializes_against_intake_update(
    api_client: TestClient, postgres_db: str, monkeypatch
) -> None:
    clock = FakeClock(T0)
    fake = FakeCrm()
    first = _submit(postgres_db, _payload(), clock)
    _run_next(postgres_db, clock, fake, "prepare_enquiry")

    session = _factory(postgres_db)()
    try:
        claim = claim_next(session, WORKER, clock)
    finally:
        session.close()
    assert claim is not None and claim.kind == "sync_contact"

    entered = threading.Event()
    release = threading.Event()
    original_record = crm_sync.record_create_success

    def paused_record(session_, claim_, contact_, operation_id_, created_):
        entered.set()
        assert release.wait(timeout=15)
        return original_record(session_, claim_, contact_, operation_id_, created_)

    monkeypatch.setattr(crm_sync, "record_create_success", paused_record)
    worker_errors: list[BaseException] = []

    def run_claim() -> None:
        thread_session = _factory(postgres_db)()
        try:
            handle_claim(thread_session, claim, clock, crm=fake)
        except BaseException as exc:
            worker_errors.append(exc)
        finally:
            thread_session.close()

    worker = threading.Thread(target=run_claim)
    worker.start()
    assert entered.wait(timeout=15)

    intake_done = threading.Event()
    intake_outcome: dict = {}

    def run_intake() -> None:
        try:
            intake_outcome["result"] = _submit(postgres_db, _v2_payload(), clock)
        except BaseException as exc:
            intake_outcome["error"] = exc
        finally:
            intake_done.set()

    intake_thread = threading.Thread(target=run_intake)
    intake_thread.start()
    time.sleep(0.5)
    # Finalization already read v1 and holds its serialization point:
    # the intake update may not commit underneath it.
    assert not intake_done.is_set(), "intake committed during sync finalization"
    assert _contact(postgres_db).version == 1
    release.set()
    worker.join(timeout=20)
    intake_thread.join(timeout=20)
    assert not worker.is_alive() and not intake_thread.is_alive()
    assert worker_errors == [], f"worker errors: {worker_errors!r}"
    assert "error" not in intake_outcome, intake_outcome.get("error")
    second = intake_outcome["result"]
    assert _contact(postgres_db).version == 2

    _run_next(postgres_db, clock, fake, "prepare_enquiry")
    _run_next(postgres_db, clock, fake, "sync_contact")
    assert len(fake.calls_of("create_contact")) == 1
    _assert_remote_v2(fake)
    contact = _contact(postgres_db)
    assert _link(postgres_db, contact.id).synced_contact_version == 2
    assert _detail(api_client, first.enquiry_id)["processing_summary"] == "ready"
    assert _detail(api_client, second.enquiry_id)["processing_summary"] == "ready"
