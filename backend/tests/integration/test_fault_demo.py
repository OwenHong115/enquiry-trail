"""Integration tests for scenario API, fault consumption and crash recovery (P6)."""

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
from app.worker.faults import FaultMatcher
from app.worker.runner import run_until_idle
from support.fake_clock import FakeClock

ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)
TOKEN = "test-sim-token"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def sim():
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, SIM_TOKEN=TOKEN, PORT=str(port))
    proc = subprocess.Popen([sys.executable, str(ROOT / "provider-sim" / "app.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/__test__/counts", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("sim did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _create_run(api_client, scenario_id="demo-happy"):
    r = api_client.post("/api/v1/scenarios", json={"scenario_id": scenario_id},
                        headers={"X-CSRF-Token": api_client.csrf_token})
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_scenario_api_create_arm_and_read(api_client, seeded_operator):
    run = _create_run(api_client)
    assert run["status"] == "pending"
    assert run["assertion_results"] == []
    r = api_client.post(f"/api/v1/scenarios/{run['id']}/faults",
                        json={"provider": "hubspot", "mode": "http_503_before_write",
                              "uses": 2, "expires_in_seconds": 600},
                        headers={"X-CSRF-Token": api_client.csrf_token})
    assert r.status_code in (200, 201), r.text
    fault = r.json()
    assert fault["remaining_uses"] == 2 and fault["status"] == "armed"
    got = api_client.get(f"/api/v1/scenarios/{run['id']}")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["id"] == run["id"]
    assert len(body["faults"]) == 1


def test_scenario_api_rejects_out_of_bounds_uses(api_client):
    run = _create_run(api_client, "demo-bounds")
    r = api_client.post(f"/api/v1/scenarios/{run['id']}/faults",
                        json={"provider": "hubspot", "mode": "http_503_before_write",
                              "uses": 4, "expires_in_seconds": 600},
                        headers={"X-CSRF-Token": api_client.csrf_token})
    assert r.status_code == 422, r.text


def test_scenario_api_requires_auth(postgres_db):
    from fastapi.testclient import TestClient
    from app.bootstrap import create_app
    from app.settings import Settings
    app = create_app(Settings(database_url=postgres_db, app_base_url="http://testserver",
                              session_secret="test-session-secret"))
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/api/v1/scenarios").status_code == 401


def test_matcher_consumes_once_and_replay_is_safe(db_session, seeded_operator):
    from app.db import fault_repo
    run = fault_repo.create_run(db_session, scenario_id="demo-consume")
    db_session.commit()
    fault = fault_repo.arm_fault(db_session, run_id=run.id, provider="hubspot",
                                 mode="http_503_before_write", uses=2,
                                 expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                                 created_by=seeded_operator)
    db_session.commit()
    matcher = FaultMatcher()
    job = uuid.uuid4()
    m1 = matcher.check(db_session, scenario_run_id=run.id, job_id=job,
                       provider="hubspot", operation="create_contact")
    db_session.commit()
    m2 = matcher.check(db_session, scenario_run_id=run.id, job_id=job,
                       provider="hubspot", operation="create_contact")
    db_session.commit()
    assert m1 == "http_503_before_write" and m2 == "http_503_before_write"
    claims = fault_repo.list_claims(db_session, run.id)
    assert len(claims) == 1
    fresh = fault_repo.get_fault(db_session, fault.id)
    assert fresh.remaining_uses == 1
    events = db_session.execute(text(
        "SELECT count(*) FROM leadops.activity_events WHERE scenario_run_id=:r AND event_type='fault.triggered'"),
        {"r": str(run.id)}).scalar_one()
    assert events == 1


def test_director_unavailable_marks_inconclusive(db_session, seeded_operator):
    from app.db import fault_repo
    run = fault_repo.create_run(db_session, scenario_id="demo-director")
    db_session.commit()

    def bad_reader(session, run_id, provider):
        raise RuntimeError("director down")

    matcher = FaultMatcher(reader=bad_reader)
    result = matcher.check(db_session, scenario_run_id=run.id, job_id=uuid.uuid4(),
                           provider="hubspot", operation="create_contact")
    db_session.commit()
    assert result is None
    fresh = fault_repo.get_run(db_session, run.id)
    assert fresh.status == "inconclusive"
    events = db_session.execute(text(
        "SELECT count(*) FROM leadops.activity_events WHERE scenario_run_id=:r AND event_type='fault.director_unavailable'"),
        {"r": str(run.id)}).scalar_one()
    assert events == 1


def test_crash_after_remote_write_recovers_by_readback(api_client, postgres_db, sim, seeded_operator):
    httpx.post(f"{sim}/__test__/reset")
    # Intake via public API, then link to a scenario run.
    payload = {"first_name": "Crash", "last_name": "Demo", "email": "crash.demo@example.com",
               "phone": "+61412345678", "postcode": "2000", "service_type": "kitchen",
               "message": "I need a kitchen renovation next month. My budget is around AUD 20,000.", "preferred_contact": "email", "acknowledge_demo": True}
    r = api_client.post("/api/v1/intake/form", json=payload, headers={"X-CSRF-Token": api_client.csrf_token,
                        "Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code in (200, 201, 202), r.text
    enquiry_id = r.json()["enquiry_id"]
    run = _create_run(api_client, "demo-crash")
    engine = create_engine(postgres_db)
    factory = sessionmaker(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE leadops.enquiries SET scenario_run_id=:r WHERE id=:e"),
                     {"r": run["id"], "e": enquiry_id})
    # Prepare in-process so the sync job exists.
    clock = FakeClock(datetime.now(timezone.utc))
    crm = HttpCrmAdapter(sim, TOKEN)
    s = factory()
    try:
        run_until_idle(lambda: factory(), uuid.uuid4(), clock, crm, max_iterations=1)
    finally:
        s.close()
    # Arm crash fault, then run the sync job in a child process.
    from app.db import fault_repo
    sess = factory()
    try:
        with sess.begin():
            fault_repo.arm_fault(sess, run_id=uuid.UUID(run["id"]), provider="hubspot",
                                 mode="crash_after_remote_write", uses=1,
                                 expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                                 created_by=seeded_operator)
    finally:
        sess.close()
    script = (
        "import uuid,sys\n"
        f"sys.path.insert(0, {str(ROOT / 'backend')!r}); sys.path.insert(0, {str(ROOT / 'backend' / 'tests')!r})\n"
        "from sqlalchemy import create_engine\nfrom sqlalchemy.orm import sessionmaker\n"
        "from app.adapters.crm_http import HttpCrmAdapter\n"
        "from app.worker.runner import run_once\nfrom support.fake_clock import FakeClock\n"
        "from datetime import datetime, timezone\n"
        f"e=create_engine({postgres_db!r}); f=sessionmaker(bind=e); s=f()\n"
        f"print('HANDLED', run_once(s, uuid.uuid4(), FakeClock(datetime.now(timezone.utc)), HttpCrmAdapter({sim!r}, {TOKEN!r}), heartbeat_interval_seconds=0))\n"
        "print('SURVIVED')\n"
    )
    env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"))
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "SURVIVED" not in proc.stdout
    counts = httpx.get(f"{sim}/__test__/counts").json()["counts"]
    assert counts["create"] == 1, proc.stdout + proc.stderr
    # Expire the lease the dead worker held, then a restarted worker reconciles.
    with engine.begin() as conn:
        conn.execute(text("UPDATE leadops.jobs SET lease_until = clock_timestamp() - interval '1 minute', next_attempt_at = clock_timestamp() - interval '1 minute' WHERE state='running'"))
    run_until_idle(lambda: factory(), uuid.uuid4(), clock, crm, max_iterations=10)
    with engine.connect() as conn:
        links = conn.execute(text("SELECT count(*) FROM leadops.crm_links")).scalar_one()
        tasks = conn.execute(text("SELECT count(*) FROM leadops.follow_up_tasks WHERE enquiry_id=:e"), {"e": enquiry_id}).scalar_one()
    assert links == 1
    assert tasks == 1
    counts_after = httpx.get(f"{sim}/__test__/counts").json()["counts"]
    assert counts_after["create"] == 1
    engine.dispose()


def _fault_setup(db_session, seeded_operator, *, uses=1, mode="http_503_before_write",
                 expires_at=None):
    from app.db import fault_repo
    run = fault_repo.create_run(db_session, scenario_id="demo-regression")
    db_session.commit()
    fault = fault_repo.arm_fault(
        db_session, run_id=run.id, provider="hubspot", mode=mode, uses=uses,
        expires_at=expires_at or (datetime.now(timezone.utc) + timedelta(minutes=10)),
        created_by=seeded_operator)
    db_session.commit()
    return run, fault


def test_try_claim_competing_duplicate_consumes_once(postgres_db, db_session, seeded_operator):
    import threading

    from app.db import fault_repo
    run, fault = _fault_setup(db_session, seeded_operator, uses=2)
    fault_row = dict(fault_repo.get_fault(db_session, fault.id))
    factory = sessionmaker(bind=create_engine(postgres_db))
    barrier = threading.Barrier(2)
    results: list = []
    errors: list = []

    def compete():
        session = factory()
        try:
            barrier.wait(timeout=10)
            outcome = fault_repo.try_claim(
                session, run_id=run.id, fault=fault_row,
                claim_identity="job-1:hubspot:create_contact")
            session.commit()
            results.append(outcome)
        except Exception as err:  # noqa: BLE001 - collected and asserted below
            errors.append(err)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=compete) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == [], errors
    assert sorted(consumed for _mode, consumed in results) == [False, True]
    claims = fault_repo.list_claims(db_session, run.id)
    assert len(claims) == 1
    assert fault_repo.get_fault(db_session, fault.id).remaining_uses == 1
    events = db_session.execute(text(
        "SELECT count(*) FROM leadops.activity_events "
        "WHERE scenario_run_id=:r AND event_type='fault.triggered'"),
        {"r": str(run.id)}).scalar_one()
    assert events == 1


def test_expired_fault_transitions_to_expired(db_session, seeded_operator):
    from app.db import fault_repo
    run, fault = _fault_setup(
        db_session, seeded_operator,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    found = fault_repo.find_armed_fault(db_session, run.id, "hubspot")
    db_session.commit()
    assert found is None
    assert fault_repo.get_fault(db_session, fault.id).status == "expired"


def _arrange_linked_job(postgres_db, *, scenario_run_id, prepare_first=True):
    from app.services.intake import submit_intake
    from app.worker.claiming import claim_next, handle_claim
    from support.fake_crm import FakeCrm  # noqa: F401 - re-exported for callers
    from support.routing_config import ROUTING_CONFIG
    factory = sessionmaker(bind=create_engine(postgres_db))
    clock = FakeClock(T0)
    worker = uuid.uuid4()
    session = factory()
    try:
        result = submit_intake(
            session,
            {"first_name": "Alex", "last_name": "Morgan",
             "email": f"fault-{uuid.uuid4().hex[:8]}@example.com",
             "phone": "+61412345678", "postcode": "2150",
             "service_type": "bathroom",
             "message": "I need a bathroom renovation next month. "
                        "My budget is around AUD 20,000.",
             "preferred_contact": "email", "acknowledge_demo": True},
            source="web", event_key=uuid.uuid4(), clock=clock,
            routing_config=ROUTING_CONFIG)
        enquiry_id = result.enquiry_id
    finally:
        session.close()
    with create_engine(postgres_db).begin() as conn:
        conn.execute(text(
            "UPDATE leadops.enquiries SET scenario_run_id=:r WHERE id=:e"),
            {"r": str(scenario_run_id), "e": str(enquiry_id)})
    if prepare_first:
        session = factory()
        try:
            prepare = claim_next(session, worker, clock)
        finally:
            session.close()
        assert prepare is not None and prepare.kind == "prepare_enquiry"
        session = factory()
        try:
            handle_claim(session, prepare, clock)
        finally:
            session.close()
    session = factory()
    try:
        claim = claim_next(session, worker, clock)
    finally:
        session.close()
    return clock, worker, claim, enquiry_id


def _fault_counts(postgres_db, run_id, fault_id):
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            remaining = conn.execute(text(
                "SELECT remaining_uses FROM leadops.demo_faults WHERE id=:f"),
                {"f": str(fault_id)}).scalar_one()
            triggered = conn.execute(text(
                "SELECT count(*) FROM leadops.activity_events "
                "WHERE scenario_run_id=:r AND event_type='fault.triggered'"),
                {"r": str(run_id)}).scalar_one()
        return remaining, triggered
    finally:
        engine.dispose()


def test_stale_lease_consumes_no_fault(postgres_db, db_session, seeded_operator):
    from app.worker.claiming import ClaimedJob, handle_claim
    from support.fake_crm import FakeCrm
    from support.fake_scheduler import FakeScheduler
    run, fault = _fault_setup(db_session, seeded_operator, uses=1)
    db_session.close()
    clock, _worker, claim, _enquiry_id = _arrange_linked_job(
        postgres_db, scenario_run_id=run.id)
    assert claim is not None and claim.kind == "sync_contact"
    stale = ClaimedJob(job_id=claim.job_id, kind=claim.kind,
                       entity_id=claim.entity_id, lease_token=uuid.uuid4(),
                       attempt_no=claim.attempt_no, cycle_no=claim.cycle_no,
                       cycle_attempt_count=claim.cycle_attempt_count)
    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        handle_claim(session, stale, clock, crm=FakeCrm(), scheduler=FakeScheduler())
    finally:
        session.close()
    remaining, triggered = _fault_counts(postgres_db, run.id, fault.id)
    assert remaining == 1
    assert triggered == 0


def test_provider_blocked_consumes_no_fault(postgres_db, db_session, seeded_operator):
    from app.worker.claiming import handle_claim
    from support.fake_crm import FakeCrm
    from support.fake_scheduler import FakeScheduler
    run, fault = _fault_setup(db_session, seeded_operator, uses=1)
    db_session.close()
    with create_engine(postgres_db).begin() as conn:
        conn.execute(text(
            "INSERT INTO leadops.integration_health "
            "(provider, status, updated_at) VALUES ('hubspot', 'blocked', clock_timestamp()) "
            "ON CONFLICT (provider) DO UPDATE SET status='blocked'"))
    clock, _worker, claim, _enquiry_id = _arrange_linked_job(
        postgres_db, scenario_run_id=run.id)
    assert claim is not None and claim.kind == "sync_contact"
    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        handle_claim(session, claim, clock, crm=FakeCrm(), scheduler=FakeScheduler())
    finally:
        session.close()
    remaining, triggered = _fault_counts(postgres_db, run.id, fault.id)
    assert remaining == 1
    assert triggered == 0


def test_scenario_lookup_failure_leaves_job_handled(postgres_db, db_session,
                                                    seeded_operator, monkeypatch):
    from app.db import fault_repo
    from app.worker import claiming
    run, _fault = _fault_setup(db_session, seeded_operator, uses=1,
                               mode="invalid_ai_response")
    # Arm for the ai provider used by the prepare path.
    fault_repo.arm_fault(
        db_session, run_id=run.id, provider="ai", mode="invalid_ai_response",
        uses=1, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        created_by=seeded_operator)
    db_session.commit()
    db_session.close()

    def broken_lookup(session, enquiry_id):
        raise RuntimeError("scenario lookup down")

    monkeypatch.setattr(claiming, "_scenario_run_id", broken_lookup)
    clock, _worker, claim, enquiry_id = _arrange_linked_job(
        postgres_db, scenario_run_id=run.id, prepare_first=False)
    assert claim is not None and claim.kind == "prepare_enquiry"
    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        claiming.handle_claim(session, claim, clock)  # must not raise
    finally:
        session.close()
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            prepared = conn.execute(text(
                "SELECT local_prepared_at FROM leadops.enquiries WHERE id=:e"),
                {"e": str(enquiry_id)}).scalar_one()
            status = conn.execute(text(
                "SELECT status FROM leadops.scenario_runs WHERE id=:r"),
                {"r": str(run.id)}).scalar_one()
            unavailable = conn.execute(text(
                "SELECT count(*) FROM leadops.activity_events "
                "WHERE scenario_run_id=:r AND event_type='fault.director_unavailable'"),
                {"r": str(run.id)}).scalar_one()
    finally:
        engine.dispose()
    assert prepared is not None
    assert status == "inconclusive"
    assert unavailable == 1


def test_finished_run_carries_assertion_results(api_client, db_session, seeded_operator):
    from app.db import fault_repo
    run = _create_run(api_client, "demo-finish")
    run_id = uuid.UUID(run["id"])
    fault_repo.arm_fault(
        db_session, run_id=run_id, provider="hubspot",
        mode="http_503_before_write", uses=1,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        created_by=seeded_operator)
    db_session.commit()
    matcher = FaultMatcher()
    mode = matcher.check(db_session, scenario_run_id=run_id, job_id=uuid.uuid4(),
                         provider="hubspot", operation="create_contact")
    db_session.commit()
    assert mode == "http_503_before_write"
    r = api_client.post(f"/api/v1/scenarios/{run['id']}/finish",
                        headers={"X-CSRF-Token": api_client.csrf_token})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["assertion_results"], body
    assert body["status"] == "passed", body
    assert body["finished_at"] is not None


def test_scenario_write_without_csrf_is_forbidden(api_client):
    r = api_client.post("/api/v1/scenarios", json={"scenario_id": "demo-csrf"})
    assert r.status_code == 403, r.text
