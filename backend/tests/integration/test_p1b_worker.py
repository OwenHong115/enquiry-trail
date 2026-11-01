"""P1b-1 persistent worker: idle wake, future due, two slots,
heartbeat/lease renewal, graceful shutdown, error isolation."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import ProviderError
from app.services.clock import SystemBusinessClock
from app.services.intake import submit_intake
from app.worker.runner import run_forever, run_once
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler
from support.routing_config import ROUTING_CONFIG

WORKER = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
PAYLOAD = {
    "first_name": "Alex", "last_name": "Morgan", "email": "alex.demo@example.com",
    "phone": "+61412345678", "postcode": "2150", "service_type": "bathroom",
    "message": "I need a bathroom renovation next month. My budget is around AUD 20,000.",
    "preferred_contact": "email", "acknowledge_demo": True,
}


def _factory(postgres_db):
    return sessionmaker(bind=create_engine(postgres_db))


def _fetch(postgres_db, sql, params=None):
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _submit(postgres_db, email=None):
    payload = dict(PAYLOAD)
    if email:
        payload["email"] = email
    clock = FakeClock(datetime.now(timezone.utc))
    session = _factory(postgres_db)()
    try:
        result = submit_intake(session, payload, source="web", event_key=uuid.uuid4(),
                               clock=clock, routing_config=ROUTING_CONFIG)
        return result.enquiry_id
    finally:
        session.close()


def _wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _start(postgres_db, fake, stop, clock=None, concurrency=1, scheduler=None):
    thread = threading.Thread(
        target=run_forever,
        args=(_factory(postgres_db), WORKER, clock or SystemBusinessClock(), fake),
        kwargs={"scheduler": scheduler or FakeScheduler(), "concurrency": concurrency,
                "idle_poll_seconds": 0.2, "heartbeat_seconds": 0.2,
                "shutdown_timeout_seconds": 5, "error_backoff_seconds": 0.2,
                "stop_event": stop, "heartbeat_interval_seconds": 0.1},
        daemon=True,
    )
    thread.start()
    return thread


def _jobs_done(postgres_db):
    rows = _fetch(postgres_db, "SELECT state FROM leadops.jobs")
    return bool(rows) and all(r.state == "succeeded" for r in rows)


def test_idle_then_submit_processed_without_restart(postgres_db):
    stop = threading.Event()
    thread = _start(postgres_db, FakeCrm(), stop)
    try:
        time.sleep(0.6)  # provably idle first
        _submit(postgres_db)
        assert _wait_for(lambda: _jobs_done(postgres_db))
        assert _fetch(postgres_db, "SELECT remote_id FROM leadops.crm_links")[0].remote_id
        heartbeat = _fetch(postgres_db, "SELECT active_job_count FROM leadops.worker_heartbeats WHERE worker_id=:w", {"w": WORKER})
        assert len(heartbeat) == 1
    finally:
        stop.set()
        thread.join(10)
    assert not thread.is_alive()
    heartbeat = _fetch(postgres_db, "SELECT active_job_count FROM leadops.worker_heartbeats WHERE worker_id=:w", {"w": WORKER})
    assert heartbeat[0].active_job_count == 0


def test_future_retry_processed_when_due_no_extra_attempts(postgres_db):
    fake = FakeCrm(lookup_script=[ProviderError("unavailable")])
    stop = threading.Event()
    thread = _start(postgres_db, fake, stop)
    try:
        _submit(postgres_db)
        # First sync fails retryable (due +1s per FakeScheduler), then
        # the retry creates successfully exactly once.
        assert _wait_for(lambda: _jobs_done(postgres_db))
        attempts = _fetch(postgres_db,
                          "SELECT count(*) AS n FROM leadops.job_attempts a JOIN leadops.jobs j ON j.id=a.job_id WHERE j.kind='sync_contact'")
        assert attempts[0].n == 2
        assert len(fake.calls_of("create_contact")) == 1
    finally:
        stop.set()
        thread.join(10)


def test_two_slots_two_jobs_once_each(postgres_db):
    fake = FakeCrm()
    stop = threading.Event()
    thread = _start(postgres_db, fake, stop, concurrency=2)
    try:
        _submit(postgres_db, "one@example.com")
        _submit(postgres_db, "two@example.com")
        assert _wait_for(lambda: _jobs_done(postgres_db))
        links = _fetch(postgres_db, "SELECT remote_id FROM leadops.crm_links")
        assert len(links) == 2
        assert len({r.remote_id for r in links}) == 2
        per_job = _fetch(postgres_db,
                         "SELECT job_id, count(*) AS n FROM leadops.job_attempts GROUP BY job_id")
        assert all(r.n == 1 for r in per_job), per_job
        assert len(fake.calls_of("create_contact")) == 2
    finally:
        stop.set()
        thread.join(10)


class _BlockingCrm(FakeCrm):
    def __init__(self, started, release):
        super().__init__()
        self._started = started
        self._release = release

    def create_contact(self, fields, context):
        self._started.set()
        self._release.wait(20)
        return super().create_contact(fields, context)


def test_job_heartbeat_renews_lease_while_blocked(postgres_db):
    _submit(postgres_db)
    started, release = threading.Event(), threading.Event()
    fake = _BlockingCrm(started, release)
    result = {}

    def work():
        session = _factory(postgres_db)()
        try:
            # prepare first (fast), then the blocking sync via run_once loop
            assert run_once(session, WORKER, SystemBusinessClock(), fake,
                            scheduler=FakeScheduler(), heartbeat_interval_seconds=0.1)
            session.close()
            session = _factory(postgres_db)()
            result["did"] = run_once(session, WORKER, SystemBusinessClock(), fake,
                                     scheduler=FakeScheduler(), heartbeat_interval_seconds=0.1)
        finally:
            session.close()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        assert started.wait(15)
        first = _fetch(postgres_db, "SELECT heartbeat_at, lease_until FROM leadops.jobs WHERE kind='sync_contact'")[0]
        time.sleep(0.8)
        later = _fetch(postgres_db, "SELECT heartbeat_at, lease_until FROM leadops.jobs WHERE kind='sync_contact'")[0]
        assert later.heartbeat_at > first.heartbeat_at
        assert later.lease_until > first.lease_until
    finally:
        release.set()
        thread.join(15)
    assert result.get("did") is True
    assert _jobs_done(postgres_db)


def test_graceful_shutdown_finishes_inflight_and_claims_no_more(postgres_db):
    stop = threading.Event()
    started, release = threading.Event(), threading.Event()
    fake = _BlockingCrm(started, release)
    thread = _start(postgres_db, fake, stop)
    try:
        _submit(postgres_db)
        assert started.wait(15)
        stop.set()  # request shutdown while in-flight
        time.sleep(0.3)
        release.set()  # let it finish within the timeout
        thread.join(15)
        assert not thread.is_alive()
        assert _jobs_done(postgres_db)
        heartbeat = _fetch(postgres_db, "SELECT active_job_count FROM leadops.worker_heartbeats WHERE worker_id=:w", {"w": WORKER})
        assert heartbeat[0].active_job_count == 0
        # No orphan claims after shutdown: a new enquiry stays pending.
        _submit(postgres_db, "after@example.com")
        time.sleep(0.5)
        states = _fetch(postgres_db, "SELECT state FROM leadops.jobs WHERE state='pending'")
        assert states
    finally:
        release.set()
        stop.set()
        thread.join(10)


def test_job_exception_isolated_loop_continues(postgres_db):
    fake = FakeCrm(lookup_script=[RuntimeError("boom")])
    stop = threading.Event()
    thread = _start(postgres_db, fake, stop)
    try:
        _submit(postgres_db, "first@example.com")
        _submit(postgres_db, "second@example.com")
        # second enquiry fully syncs despite the first sync raising
        def second_done():
            rows = _fetch(postgres_db,
                          "SELECT count(*) AS n FROM leadops.crm_links")
            return rows[0].n >= 1
        assert _wait_for(second_done)
    finally:
        stop.set()
        thread.join(10)
    assert not thread.is_alive()


def test_worker_settings_validation():
    import pytest
    from app.settings import Settings
    with pytest.raises(ValueError):
        Settings(database_url="x", session_secret="s", worker_concurrency=0)
    with pytest.raises(ValueError):
        Settings(database_url="x", session_secret="s", worker_idle_poll_seconds=0)


class _FlakyFactory:
    """Fails session creation a bounded number of times, then works."""

    def __init__(self, real, fail_times=0, fail_heartbeat_times=0):
        self._real = real
        self._fail = fail_times
        self._fail_hb = fail_heartbeat_times
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            if threading.current_thread().name == "worker-heartbeat" and self._fail_hb > 0:
                self._fail_hb -= 1
                raise RuntimeError("database temporarily unavailable")
            if self._fail > 0:
                self._fail -= 1
                raise RuntimeError("database temporarily unavailable")
        return self._real()


def _start_factory(postgres_db, factory, fake, stop, concurrency=1):
    errors = []

    def target():
        try:
            run_forever(factory, WORKER, SystemBusinessClock(), fake,
                        scheduler=FakeScheduler(), concurrency=concurrency,
                        idle_poll_seconds=0.2, heartbeat_seconds=0.2,
                        shutdown_timeout_seconds=5, error_backoff_seconds=0.1,
                        stop_event=stop, heartbeat_interval_seconds=0.1)
        except BaseException as exc:  # captured, asserted below
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, errors


def test_db_outage_factory_recovers_and_processes(postgres_db):
    factory = _FlakyFactory(_factory(postgres_db), fail_times=6)
    stop = threading.Event()
    thread, errors = _start_factory(postgres_db, factory, FakeCrm(), stop)
    try:
        _submit(postgres_db)
        assert _wait_for(lambda: _jobs_done(postgres_db))
        assert not stop.is_set()  # worker never died / never self-stopped
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(10)
    assert not thread.is_alive()
    assert errors == [], errors


def test_failed_initial_heartbeat_does_not_kill_loop(postgres_db):
    factory = _FlakyFactory(_factory(postgres_db), fail_heartbeat_times=3)
    stop = threading.Event()
    thread, errors = _start_factory(postgres_db, factory, FakeCrm(), stop)
    try:
        def heartbeat_row():
            return bool(_fetch(postgres_db, "SELECT worker_id FROM leadops.worker_heartbeats WHERE worker_id=:w", {"w": WORKER}))
        assert _wait_for(heartbeat_row)
    finally:
        stop.set()
        thread.join(10)
    assert errors == [], errors


def test_active_job_count_idle_blocked_done(postgres_db):
    stop = threading.Event()
    thread = _start(postgres_db, FakeCrm(), stop)
    try:
        def active():
            rows = _fetch(postgres_db, "SELECT active_job_count FROM leadops.worker_heartbeats WHERE worker_id=:w", {"w": WORKER})
            return rows[0].active_job_count if rows else None
        assert _wait_for(lambda: active() == 0)
    finally:
        stop.set()
        thread.join(10)

    started, release = threading.Event(), threading.Event()
    stop2 = threading.Event()
    thread2 = _start(postgres_db, _BlockingCrm(started, release), stop2)
    try:
        _submit(postgres_db)
        assert started.wait(15)
        def active2():
            rows = _fetch(postgres_db, "SELECT active_job_count FROM leadops.worker_heartbeats WHERE worker_id=:w", {"w": WORKER})
            return rows[0].active_job_count if rows else None
        assert _wait_for(lambda: active2() == 1)
        release.set()
        assert _wait_for(lambda: _jobs_done(postgres_db))
        assert _wait_for(lambda: active2() == 0)
    finally:
        release.set()
        stop2.set()
        thread2.join(10)
    rows = _fetch(postgres_db, "SELECT active_job_count FROM leadops.worker_heartbeats WHERE worker_id=:w", {"w": WORKER})
    assert rows[0].active_job_count == 0
