"""S5 red tests: CRM sync happy path and A10 field preservation.

Source contracts (transcribed by hand; plan package v3.0):
- Port signatures and field rules (omit null lastname/phone on
  create; patch only readback-confirmed empty fields; never email)
  come from the adapter contract.
- A10: nonblank remote fields are preserved, each preservation is
  recorded as a masked crm.field_preserved event, and the link
  still reaches synced at the contact's current version.

The mask format is not defined in the contracts: these tests assert
only that raw values never appear as masked values and that masked
values are non-empty. The format itself is Green's choice.
Test 4 is the O03 capability-unavailable negative branch in the
mock profile; it is not connected-mode evidence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import CrmPort, FoundContact
from app.services.intake import IntakeResult, submit_intake
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _payload(**overrides: object) -> dict:
    payload: dict = {
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
    payload.update(overrides)
    return payload


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _submit(postgres_db: str, payload: dict, clock: FakeClock) -> IntakeResult:
    session = _factory(postgres_db)()
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


def _claim(postgres_db: str, clock: FakeClock):
    session = _factory(postgres_db)()
    try:
        return claim_next(session, WORKER, clock)
    finally:
        session.close()


def _handle(postgres_db: str, claim, clock: FakeClock, crm: CrmPort | None = None):
    session = _factory(postgres_db)()
    try:
        handle_claim(session, claim, clock, crm=crm)
    finally:
        session.close()


def _contact(postgres_db: str, enquiry_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT c.id, c.version FROM leadops.contacts c "
        "JOIN leadops.enquiries e ON e.contact_id = c.id "
        "WHERE e.id = :enquiry_id",
        {"enquiry_id": enquiry_id},
    )[0]


def _insert_sync_job(postgres_db: str, enquiry_id: uuid.UUID) -> uuid.UUID:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            # Park the intake's prepare job beyond the test horizon
            # so the claim below deterministically picks the sync
            # job arranged here.
            conn.execute(
                text(
                    "UPDATE leadops.jobs SET next_attempt_at = :due "
                    "WHERE kind = 'prepare_enquiry'"
                ),
                {"due": datetime(2027, 1, 1, tzinfo=timezone.utc)},
            )
            return conn.execute(
                text(
                    "INSERT INTO leadops.jobs (kind, entity_id, dedupe_key, "
                    "state, next_attempt_at) VALUES ('sync_contact', "
                    ":enquiry_id, :dedupe, 'pending', :due) RETURNING id"
                ),
                {
                    "enquiry_id": enquiry_id,
                    "dedupe": f"sync_contact:{enquiry_id}",
                    "due": T0,
                },
            ).scalar_one()
    finally:
        engine.dispose()


def _claim_and_handle_sync(postgres_db: str, clock: FakeClock, fake: FakeCrm):
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.kind == "sync_contact"
    _handle(postgres_db, claim, clock, crm=fake)
    return claim


def _crm_link(postgres_db: str, contact_id: uuid.UUID):
    rows = _fetch(
        postgres_db,
        "SELECT remote_id, sync_status, synced_contact_version "
        "FROM leadops.crm_links WHERE contact_id = :contact_id",
        {"contact_id": contact_id},
    )
    assert len(rows) == 1
    return rows[0]


def _events(postgres_db: str, event_type: str) -> list:
    return _fetch(
        postgres_db,
        "SELECT details_json FROM leadops.activity_events "
        "WHERE event_type = :event_type",
        {"event_type": event_type},
    )


def _job_state(postgres_db: str, job_id: uuid.UUID) -> str:
    return _fetch(
        postgres_db,
        "SELECT state FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0].state


def test_prepare_enqueues_sync_job(postgres_db: str) -> None:
    clock = FakeClock(T0)
    result = _submit(postgres_db, _payload(), clock)
    contact = _contact(postgres_db, result.enquiry_id)

    prepare = _claim(postgres_db, clock)
    assert prepare is not None
    _handle(postgres_db, prepare, clock)

    jobs = _fetch(
        postgres_db,
        "SELECT entity_id, state, dedupe_key, next_attempt_at "
        "FROM leadops.jobs WHERE kind = 'sync_contact'",
    )
    # Contract: a successful prepare enqueues exactly one pending
    # sync job for this enquiry, keyed by the contact, due now.
    assert len(jobs) == 1
    job = jobs[0]
    assert job.entity_id == result.enquiry_id
    assert job.state == "pending"
    assert job.dedupe_key == f"sync_contact:{contact.id}"
    assert job.next_attempt_at == T0


def test_sync_creates_missing_remote_contact(postgres_db: str) -> None:
    clock = FakeClock(T0)
    # Null lastname/phone exercise the create contract's omit rule.
    result = _submit(postgres_db, _payload(last_name=None, phone=None), clock)
    contact = _contact(postgres_db, result.enquiry_id)
    job_id = _insert_sync_job(postgres_db, result.enquiry_id)

    fake = FakeCrm()
    claim = _claim_and_handle_sync(postgres_db, clock, fake)

    creates = fake.calls_of("create_contact")
    assert len(creates) == 1
    # Contract: create carries email/firstname and omits null
    # lastname and phone entirely.
    assert creates[0].fields == {
        "email": "alex.demo@example.com",
        "firstname": "Alex",
    }
    assert fake.calls_of("patch_contact") == []

    link = _crm_link(postgres_db, contact.id)
    assert link.remote_id == fake.create_remote_id
    assert link.sync_status == "synced"
    assert link.synced_contact_version == contact.version

    synced = _events(postgres_db, "crm.synced")
    assert len(synced) == 1
    assert synced[0].details_json["remote_id"] == fake.create_remote_id
    assert synced[0].details_json["contact_version"] == contact.version
    assert _job_state(postgres_db, job_id) == "succeeded"
    assert claim.job_id == job_id


def _found_fake(**kwargs: object) -> FakeCrm:
    return FakeCrm(
        by_email=FoundContact(
            remote_id="hs-existing-9",
            email="alex.demo@example.com",
            firstname="Alexander",
            lastname=None,
            phone="+61999999999",
        ),
        **kwargs,  # type: ignore[arg-type]
    )


def _assert_field_preserved(postgres_db: str) -> None:
    events = _events(postgres_db, "crm.field_preserved")
    assert len(events) == 2
    by_property = {e.details_json["property"]: e.details_json for e in events}
    assert set(by_property) == {"firstname", "phone"}
    # Contract A10: masked values are recorded per preserved field;
    # a masked value never equals the raw value and is never empty
    # (the format itself is undefined — see module docstring).
    raws = {
        "firstname": ("Alex", "Alexander"),
        "phone": ("+61412345678", "+61999999999"),
    }
    for prop, (local_raw, remote_raw) in raws.items():
        details = by_property[prop]
        assert details["local_masked"] not in ("", local_raw)
        assert details["remote_masked"] not in ("", remote_raw)


def test_a10_preserves_nonblank_remote_fields(postgres_db: str) -> None:
    clock = FakeClock(T0)
    result = _submit(postgres_db, _payload(), clock)
    contact = _contact(postgres_db, result.enquiry_id)
    job_id = _insert_sync_job(postgres_db, result.enquiry_id)

    fake = _found_fake()
    _claim_and_handle_sync(postgres_db, clock, fake)

    # Contract A10/ContactFieldPatch: only the readback-confirmed
    # empty field (lastname) is patched, with the local value.
    patches = fake.calls_of("patch_contact")
    assert len(patches) == 1
    assert patches[0].fields["remote_id"] == "hs-existing-9"
    assert patches[0].fields["lastname"] == "Morgan"
    assert "firstname" not in patches[0].fields
    assert "phone" not in patches[0].fields
    assert fake.calls_of("create_contact") == []

    _assert_field_preserved(postgres_db)

    link = _crm_link(postgres_db, contact.id)
    assert link.remote_id == "hs-existing-9"
    assert link.sync_status == "synced"
    assert link.synced_contact_version == contact.version
    assert _job_state(postgres_db, job_id) == "succeeded"


def test_a10_capability_off_still_syncs_without_patch(postgres_db: str) -> None:
    # O03 negative branch (mock profile): patch capability
    # unavailable means no patch call, but preservation is still
    # recorded and the link still syncs. Not connected-mode proof.
    clock = FakeClock(T0)
    result = _submit(postgres_db, _payload(), clock)
    contact = _contact(postgres_db, result.enquiry_id)
    job_id = _insert_sync_job(postgres_db, result.enquiry_id)

    fake = _found_fake(patch_supported=False)
    _claim_and_handle_sync(postgres_db, clock, fake)

    assert fake.calls_of("patch_contact") == []
    assert fake.calls_of("create_contact") == []
    _assert_field_preserved(postgres_db)

    link = _crm_link(postgres_db, contact.id)
    assert link.remote_id == "hs-existing-9"
    assert link.sync_status == "synced"
    assert link.synced_contact_version == contact.version
    assert _job_state(postgres_db, job_id) == "succeeded"
