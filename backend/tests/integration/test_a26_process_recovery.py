"""A26 process-level recovery: kill and restart the real worker process.

Starts app.worker.runner.main (the same entry the compose worker runs,
via the leadops-worker console script) as a subprocess against the test
PostgreSQL database and the real provider-sim over HTTP. The worker is
hard-killed while a sync job is in flight (the simulator is slowed so
the claim is observable), then a fresh worker process is started. The
restarted worker must recover the orphaned lease and finish exactly
once: one crm_links row, one follow-up task, one provider create, the
enquiry ready/synced, and no duplicate notification or event rows.

The orphaned lease is expired by SQL after the kill (the same
acceleration the fault-demo suite uses); waiting out the real 60 s
lease would only make the test slower, not more honest. The process
kill itself is real: subprocess.kill(), with the exit observed.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.intake import submit_intake
from support.fake_clock import FakeClock
from support.routing_config import ROUTING_CONFIG

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
TOKEN = "test-sim-token"
PAYLOAD = {
    "first_name": "A26", "last_name": "Recovery",
    "email": "a26.recovery@example.com",
    "phone": "+61412345678", "postcode": "2000",
    "service_type": "kitchen",
    "message": "I need a kitchen renovation next month. My budget is around AUD 20,000.",
    "preferred_contact": "email", "acknowledge_demo": True,
}


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
    env = dict(os.environ, SIM_TOKEN=TOKEN, PORT=str(port), SIM_SLOW_SECONDS="4")
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
            raise RuntimeError("provider-sim did not start")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _scalar(postgres_db, sql, params=None):
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).scalar_one()
    finally:
        engine.dispose()


def _wait_for(predicate, timeout=30.0, description="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {description}")


def _start_worker(postgres_db, sim_base, log_path):
    """Start the real worker runner as a subprocess; fail loudly."""
    env = dict(
        os.environ,
        DATABASE_URL=postgres_db,
        SESSION_SECRET="a26-test-session-secret",
        CRM_MODE="http",
        CRM_BASE_URL=sim_base,
        CRM_TOKEN=TOKEN,
        SLACK_MODE="disabled",
        EXTRACTOR_MODE="fixture",
        WORKER_CONCURRENCY="1",
        WORKER_IDLE_POLL_SECONDS="0.2",
        WORKER_HEARTBEAT_SECONDS="0.5",
        WORKER_SHUTDOWN_TIMEOUT_SECONDS="5",
        WORKER_ERROR_BACKOFF_SECONDS="0.2",
        PYTHONPATH=str(BACKEND),
    )
    log_file = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-c", "from app.worker.runner import main; main()"],
        cwd=str(BACKEND), env=env, stdout=log_file, stderr=subprocess.STDOUT,
    )
    proc._a26_log_file = log_file  # type: ignore[attr-defined]
    return proc


def _stop_worker(proc):
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
    finally:
        log_file = getattr(proc, "_a26_log_file", None)
        if log_file is not None:
            log_file.close()


def _worker_log_tail(log_path, limit=4000):
    try:
        return Path(log_path).read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return "<worker log unavailable>"


def test_killed_worker_restart_finishes_exactly_once(postgres_db, sim, tmp_path):
    httpx.post(f"{sim}/__test__/reset", timeout=5)
    # Slow only the create call so the claimed sync job stays in flight
    # long enough to be observed and killed mid-work.
    httpx.post(f"{sim}/__test__/fault", json={"mode": "slow", "path": "create"}, timeout=5)

    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        result = submit_intake(
            session, dict(PAYLOAD), source="web", event_key=uuid.uuid4(),
            clock=FakeClock(datetime.now(timezone.utc)),
            routing_config=ROUTING_CONFIG,
        )
        enquiry_id = str(result.enquiry_id)
    finally:
        session.close()
    contact_id = str(_scalar(
        postgres_db,
        "SELECT contact_id FROM leadops.enquiries WHERE id=:e", {"e": enquiry_id},
    ))

    worker1_log = tmp_path / "worker1.log"
    worker2_log = tmp_path / "worker2.log"
    worker1 = _start_worker(postgres_db, sim, worker1_log)
    worker2 = None
    try:
        # The worker must claim the sync job and be inside the slowed
        # provider create when it is killed.
        def sync_running():
            if worker1.poll() is not None:
                raise AssertionError(
                    "first worker exited before claiming work: "
                    f"exit={worker1.returncode} log={_worker_log_tail(worker1_log)}"
                )
            return _scalar(
                postgres_db,
                "SELECT count(*) FROM leadops.jobs "
                "WHERE kind='sync_contact' AND state='running'",
            ) == 1

        _wait_for(sync_running, timeout=30, description="sync job claimed by first worker")
        # Claimed is not yet inside the provider call: wait until the
        # simulator has received the create (its counter increments on
        # receipt, before the slow commit), so the kill lands mid-work.
        _wait_for(
            lambda: httpx.get(f"{sim}/__test__/counts", timeout=2).json()["counts"]["create"] == 1,
            timeout=15, description="simulator to receive the in-flight create",
        )

        worker1.kill()
        exit_code = worker1.wait(timeout=10)
        assert worker1.poll() is not None, "killed worker process is still running"
        assert exit_code != 0, f"killed worker reported clean exit code {exit_code}"
        # The kill left real orphaned work behind, not a finished job.
        assert _scalar(
            postgres_db,
            "SELECT count(*) FROM leadops.jobs "
            "WHERE kind='sync_contact' AND state='running'",
        ) == 1
        # The slowed simulator still commits its create after the client
        # dies; wait until that remote write is visible before restart.
        _wait_for(
            lambda: httpx.get(f"{sim}/__test__/counts", timeout=2).json()["contacts"] == 1,
            timeout=20, description="simulator remote create to commit",
        )

        # Accelerate lease expiry (the dead worker cannot renew), then
        # restart a fresh worker process on the same database and sim.
        engine = create_engine(postgres_db)
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE leadops.jobs SET lease_until = clock_timestamp() - interval '1 minute', "
                    "next_attempt_at = clock_timestamp() - interval '1 minute' "
                    "WHERE state='running'"
                ))
        finally:
            engine.dispose()

        worker2 = _start_worker(postgres_db, sim, worker2_log)

        def all_jobs_succeeded():
            if worker2.poll() is not None:
                raise AssertionError(
                    "restarted worker exited before draining: "
                    f"exit={worker2.returncode} log={_worker_log_tail(worker2_log)}"
                )
            states = _scalar(
                postgres_db,
                "SELECT count(*) FROM leadops.jobs WHERE state <> 'succeeded'",
            )
            return states == 0

        _wait_for(all_jobs_succeeded, timeout=60, description="restarted worker to drain to idle")
    finally:
        _stop_worker(worker1)
        if worker2 is not None:
            _stop_worker(worker2)

    counts = httpx.get(f"{sim}/__test__/counts", timeout=5).json()
    assert counts["counts"]["create"] == 1, counts
    assert counts["contacts"] == 1, counts
    assert _scalar(
        postgres_db,
        "SELECT count(*) FROM leadops.crm_links WHERE contact_id=:c",
        {"c": contact_id},
    ) == 1
    assert _scalar(
        postgres_db,
        "SELECT count(*) FROM leadops.follow_up_tasks WHERE enquiry_id=:e",
        {"e": enquiry_id},
    ) == 1
    assert _scalar(
        postgres_db,
        "SELECT count(*) FROM leadops.jobs WHERE kind='sync_contact'",
    ) == 1
    # Terminal ready/synced, using the read model's own definition:
    # prepared locally and the link synced at the required version.
    assert _scalar(
        postgres_db,
        "SELECT count(*) FROM leadops.enquiries e "
        "JOIN leadops.crm_links l ON l.contact_id = e.contact_id "
        "WHERE e.id=:e AND e.local_prepared_at IS NOT NULL "
        "AND l.sync_status='synced' "
        "AND l.synced_contact_version >= e.required_contact_version",
        {"e": enquiry_id},
    ) == 1
    # No duplicate notifications (Slack is disabled, so none at all) and
    # no duplicated lifecycle events for this enquiry.
    assert _scalar(
        postgres_db,
        "SELECT count(*) FROM leadops.notifications WHERE enquiry_id=:e",
        {"e": enquiry_id},
    ) == 0
    for event_type in ("enquiry.received", "enquiry.prepared", "crm.synced"):
        assert _scalar(
            postgres_db,
            "SELECT count(*) FROM leadops.activity_events "
            "WHERE enquiry_id=:e AND event_type=:t",
            {"e": enquiry_id, "t": event_type},
        ) == 1, event_type
