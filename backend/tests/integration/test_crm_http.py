"""Real-HTTP CRM adapter + provider simulator tests.

The simulator runs as a subprocess on a real loopback socket; the
adapter uses real httpx requests (no ASGI transport). Exactly one
write per attempt is proven from simulator counters.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.adapters.crm_http import HttpCrmAdapter
from app.ports.crm import (
    CallContext, ContactFieldPatch, CreateContact, FoundContact, NotFound, ProviderError,
)
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock

TOKEN = "test-sim-token"
ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)
WORKER = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def sim():
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, SIM_TOKEN=TOKEN, SIM_SLOW_SECONDS="2", PORT=str(port))
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "provider-sim" / "app.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/__test__/counts", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("provider simulator did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(autouse=True)
def _reset(sim):
    httpx.post(f"{sim}/__test__/reset")
    yield


def _counts(sim) -> dict:
    return httpx.get(f"{sim}/__test__/counts").json()


def _fault(sim, mode: str, path: str = "any") -> None:
    httpx.post(f"{sim}/__test__/fault", json={"mode": mode, "path": path})


def _ctx(deadline: float = 15.0) -> CallContext:
    return CallContext(None, uuid.uuid4(), 1, 1, uuid.uuid4(), deadline)


def _adapter(sim, token: str = TOKEN) -> HttpCrmAdapter:
    return HttpCrmAdapter(sim, token)


def _fields(email: str = "alex.demo@example.com") -> CreateContact:
    return CreateContact(email=email, firstname="Alex", lastname="Morgan", phone="+61412345678")


def _code(fn) -> str:
    with pytest.raises(ProviderError) as exc:
        fn()
    return exc.value.code


def test_success_create_lookup_patch_probe(sim):
    adapter = _adapter(sim)
    assert isinstance(adapter.get_contact_by_email("alex.demo@example.com", _ctx()), NotFound)
    created = adapter.create_contact(_fields(), _ctx())
    assert created.remote_id.startswith("sim-")
    assert adapter.get_contact_by_email(created.email, _ctx()).remote_id == created.remote_id
    assert adapter.get_contact_by_id(created.remote_id, _ctx()).email == created.email
    patched = adapter.patch_contact(created.remote_id, ContactFieldPatch(phone="+61400000000"), _ctx())
    assert patched.phone == "+61400000000"
    probe = adapter.probe(_ctx())
    assert probe.ok and probe.supports_patch
    counts = _counts(sim)["counts"]
    assert counts["create"] == 1 and counts["patch"] == 1


def test_create_validation_and_auth(sim):
    adapter = _adapter(sim)
    assert _code(lambda: adapter.create_contact(CreateContact(email="a@b.c", firstname=""), _ctx())) == "validation_failed"
    bad = _adapter(sim, token="wrong")
    assert _code(lambda: bad.get_contact_by_email("a@b.c", _ctx())) == "auth_failed"
    assert _code(lambda: bad.create_contact(_fields(), _ctx())) == "auth_failed"
    assert _counts(sim)["contacts"] == 0


def test_create_409_reads_back_and_links_existing(sim):
    adapter = _adapter(sim)
    created = adapter.create_contact(_fields(), _ctx())
    again = adapter.create_contact(_fields(), _ctx())
    assert again.remote_id == created.remote_id
    assert _counts(sim)["contacts"] == 1
    # Raw duplicate is a definite 409 from the simulator.
    raw = httpx.post(f"{sim}/contacts", json={"email": _fields().email, "firstname": "Alex"},
                     headers={"Authorization": f"Bearer {TOKEN}"})
    assert raw.status_code == 409


def test_create_429_passes_retry_after(sim):
    _fault(sim, "rate_limited", "create")
    with pytest.raises(ProviderError) as exc:
        _adapter(sim).create_contact(_fields(), _ctx())
    assert exc.value.code == "rate_limited"
    assert exc.value.retry_after_seconds == 7
    assert _counts(sim)["counts"]["create"] == 1


def test_500_before_write_is_unknown_and_no_contact(sim):
    _fault(sim, "error_before_write", "create")
    assert _code(lambda: _adapter(sim).create_contact(_fields(), _ctx())) == "result_unknown"
    assert _counts(sim) == {"counts": {"lookup_email": 0, "lookup_id": 0, "create": 1, "patch": 0, "probe": 0}, "contacts": 0}


def test_500_after_commit_is_unknown_then_readback_finds(sim):
    _fault(sim, "error_after_commit", "create")
    assert _code(lambda: _adapter(sim).create_contact(_fields(), _ctx())) == "result_unknown"
    assert _counts(sim)["counts"]["create"] == 1
    assert _counts(sim)["contacts"] == 1
    found = _adapter(sim).get_contact_by_email(_fields().email, _ctx())
    assert isinstance(found, FoundContact)


def test_disconnect_after_commit_is_unknown_and_committed(sim):
    # Real connection close without a response (RemoteProtocolError),
    # not a delayed 500: the write committed, result is unknown, and
    # exactly one create was sent.
    _fault(sim, "disconnect_after_commit", "create")
    assert _code(lambda: _adapter(sim).create_contact(_fields(), _ctx())) == "result_unknown"
    assert _counts(sim)["counts"]["create"] == 1
    assert _counts(sim)["contacts"] == 1
    found = _adapter(sim).get_contact_by_email(_fields().email, _ctx())
    assert isinstance(found, FoundContact)


def test_timeout_after_write_is_unknown_within_deadline(sim):
    _fault(sim, "timeout_after_commit", "create")
    start = time.monotonic()
    assert _code(lambda: _adapter(sim).create_contact(_fields(), _ctx(deadline=0.5))) == "result_unknown"
    elapsed = time.monotonic() - start
    assert elapsed < 0.5 + 1.0
    assert _counts(sim)["counts"]["create"] == 1
    assert _counts(sim)["contacts"] == 1


def test_slow_lookup_beyond_deadline(sim):
    start = time.monotonic()
    _fault(sim, "slow", "lookup")
    assert _code(lambda: _adapter(sim).get_contact_by_email("a@b.c", _ctx(deadline=0.5))) == "unavailable"
    assert time.monotonic() - start < 0.5 + 1.0


def test_patch_404_and_probe_failures_and_malformed(sim):
    adapter = _adapter(sim)
    assert _code(lambda: adapter.patch_contact("sim-missing", ContactFieldPatch(phone="x"), _ctx())) == "not_found"
    assert _adapter(sim, token="wrong").probe(_ctx()).ok is False
    _fault(sim, "error_before_write", "probe")
    assert adapter.probe(_ctx()).ok is False
    _fault(sim, "malformed", "lookup")
    assert _code(lambda: adapter.get_contact_by_email("a@b.c", _ctx())) == "invalid_response"


def test_connect_failed_before_send(sim):
    dead = HttpCrmAdapter(f"http://127.0.0.1:{_free_port()}", TOKEN)
    assert _code(lambda: dead.get_contact_by_email("a@b.c", _ctx())) == "connect_failed"
    assert _code(lambda: dead.create_contact(_fields(), _ctx())) == "connect_failed"


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _drain(postgres_db: str, clock: FakeClock, crm) -> None:
    for _ in range(4):
        session = _factory(postgres_db)()
        try:
            claim = claim_next(session, WORKER, clock)
            if claim is None:
                return
            handle_claim(session, claim, clock, crm=crm)
        finally:
            session.close()


def test_worker_commit_then_500_readback_reconciles_one_create(sim, postgres_db: str):
    from app.services.intake import submit_intake
    from support.routing_config import ROUTING_CONFIG

    clock = FakeClock(T0)
    session = _factory(postgres_db)()
    try:
        submit_intake(session, {
            "first_name": "Alex", "last_name": "Morgan", "email": "alex.demo@example.com",
            "phone": "+61412345678", "postcode": "2150", "service_type": "bathroom",
            "message": "I need a bathroom renovation next month. My budget is around AUD 20,000.",
            "preferred_contact": "email", "acknowledge_demo": True,
        }, source="web", event_key=uuid.uuid4(), clock=clock, routing_config=ROUTING_CONFIG)
    finally:
        session.close()

    adapter = _adapter(sim)
    _fault(sim, "error_after_commit", "create")
    _drain(postgres_db, clock, adapter)  # prepare + sync(unknown)
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            job = conn.execute(text("SELECT state, next_attempt_at FROM leadops.jobs WHERE kind='sync_contact'")).one()
    finally:
        engine.dispose()
    assert job.state == "reconciling"
    assert job.next_attempt_at == T0 + timedelta(seconds=1)
    assert _counts(sim)["counts"]["create"] == 1

    clock.advance_to(T0 + timedelta(seconds=1))
    _drain(postgres_db, clock, adapter)  # readback finds, reconciles
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            job = conn.execute(text("SELECT state FROM leadops.jobs WHERE kind='sync_contact'")).one()
            link = conn.execute(text("SELECT remote_id, sync_status FROM leadops.crm_links")).one()
            op = conn.execute(text("SELECT status FROM leadops.external_operations")).one()
    finally:
        engine.dispose()
    assert job.state == "succeeded"
    assert link.remote_id.startswith("sim-") and link.sync_status == "synced"
    assert op.status == "reconciled"
    assert _counts(sim)["counts"]["create"] == 1, _counts(sim)


def test_end_to_end_intake_worker_simulator_detail_ready(sim, api_client, postgres_db: str):
    receipt = api_client.post(
        "/api/v1/intake/form",
        json={
            "first_name": "Alex", "last_name": "Morgan", "email": "alex.demo@example.com",
            "phone": "+61412345678", "postcode": "2150", "service_type": "bathroom",
            "message": "I need a bathroom renovation next month. My budget is around AUD 20,000.",
            "preferred_contact": "email", "acknowledge_demo": True,
        },
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-CSRF-Token": api_client.csrf_token},
    )
    # The intake payload shape is owned by the API suite; if this
    # submit shape is rejected, fall back is not acceptable.
    assert receipt.status_code == 202, receipt.text
    body = receipt.json()
    clock = FakeClock(datetime.now(timezone.utc))
    _drain(postgres_db, clock, _adapter(sim))
    detail = api_client.get(body["status_url"])
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert detail_body["processing_summary"] == "ready"
    assert detail_body["crm"]["status"] == "synced"
    assert detail_body["crm"]["remote_id"].startswith("sim-")
    counts = _counts(sim)["counts"]
    assert counts["create"] == 1 and counts["lookup_email"] >= 1
