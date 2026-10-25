"""Worker entrypoint: claim and handle jobs, persistently.

run_once is the single-step primitive the tests drive; run_until_idle
loops it until the queue drains (kept for tests); run_forever is the
persistent runtime main() runs: bounded-concurrency slots with
independent sessions, worker and per-job heartbeats with lease
renewal, idle sleep until the earliest future job, bounded error
backoff, and graceful shutdown on SIGINT/SIGTERM or a stop event.
"""

from __future__ import annotations

import signal
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timezone

from sqlalchemy.orm import Session

from app.db import job_repo, worker_repo
from app.db.session import make_engine, make_session_factory
from app.logging import configure_logging, log_event
from app.ports.clock import BusinessClock
from app.ports.crm import CrmPort, ProviderError
from app.ports.scheduler import Scheduler
from app.services.clock import SystemBusinessClock
from app.settings import Settings
from app.worker.claiming import claim_next, handle_claim

_LEASE_SECONDS = 60


def _renew_loop(bind, claim, interval: float, stop: threading.Event) -> None:
    """Renew one job's lease (DB clock) until told to stop or fenced out."""
    while not stop.wait(interval):
        session = Session(bind=bind)
        try:
            with session.begin():
                renewed = job_repo.renew_lease(
                    session, claim.job_id, claim.lease_token, _LEASE_SECONDS
                )
            if renewed == 0:
                # Fence lost: another worker owns the job. Stop
                # renewing; the handler's final fence will no-op.
                log_event("job.heartbeat_fence_lost", job_id=str(claim.job_id),
                          attempt_no=claim.attempt_no)
                return
        except Exception:
            session.rollback()
            return
        finally:
            session.close()


def run_once(
    session: Session,
    worker_id: uuid.UUID,
    clock: BusinessClock,
    crm: CrmPort,
    scheduler: Scheduler | None = None,
    heartbeat_interval_seconds: float = 5.0,
) -> bool:
    """Claim and handle at most one due job (True when one was handled)."""
    claim = claim_next(session, worker_id, clock)
    if claim is None:
        return False
    stop_heartbeat = threading.Event()
    heartbeat_thread = None
    if heartbeat_interval_seconds and heartbeat_interval_seconds > 0:
        heartbeat_thread = threading.Thread(
            target=_renew_loop,
            args=(session.get_bind(), claim, heartbeat_interval_seconds, stop_heartbeat),
            daemon=True, name="job-heartbeat",
        )
        heartbeat_thread.start()
    try:
        handle_claim(session, claim, clock, crm=crm, scheduler=scheduler)
    except Exception as err:
        session.rollback()
        log_event(
            "job.error",
            job_id=str(claim.job_id),
            attempt_no=claim.attempt_no,
            error_code=(
                err.code if isinstance(err, ProviderError) else type(err).__name__
            ),
        )
        return True
    finally:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=heartbeat_interval_seconds + 2)
    outcome = job_repo.get_job_state(session, claim.job_id)
    session.rollback()
    log_event(
        "job.handled",
        job_id=str(claim.job_id),
        kind=claim.kind,
        attempt_no=claim.attempt_no,
        outcome=outcome,
    )
    return True


def run_until_idle(
    session_factory: Callable[[], Session],
    worker_id: uuid.UUID,
    clock: BusinessClock,
    crm: CrmPort,
    scheduler: Scheduler | None = None,
    max_iterations: int | None = None,
    heartbeat_interval_seconds: float = 5.0,
) -> int:
    """Run jobs until none is due; return how many were handled."""
    handled = 0
    while max_iterations is None or handled < max_iterations:
        session = session_factory()
        try:
            if not run_once(session, worker_id, clock, crm, scheduler,
                            heartbeat_interval_seconds=heartbeat_interval_seconds):
                break
        finally:
            session.close()
        handled += 1
    return handled


def _upsert_worker_heartbeat(session_factory, worker_id, active: int) -> None:
    session = session_factory()
    try:
        with session.begin():
            worker_repo.upsert_heartbeat(session, worker_id, active)
    finally:
        session.close()


def _idle_sleep_seconds(session_factory, clock, idle_poll_seconds: float) -> float:
    session = session_factory()
    try:
        earliest = job_repo.earliest_next_attempt_at(session)
        session.rollback()
    finally:
        session.close()
    if earliest is None:
        return idle_poll_seconds
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    until_due = (earliest - clock.now_utc()).total_seconds()
    if until_due <= 0:
        return 0.05
    return min(idle_poll_seconds, until_due)


def run_forever(
    session_factory: Callable[[], Session],
    worker_id: uuid.UUID,
    clock: BusinessClock,
    crm: CrmPort,
    scheduler: Scheduler | None = None,
    *,
    concurrency: int = 2,
    idle_poll_seconds: float = 1.0,
    heartbeat_seconds: float = 5.0,
    shutdown_timeout_seconds: float = 30.0,
    error_backoff_seconds: float = 1.0,
    stop_event: threading.Event | None = None,
    heartbeat_interval_seconds: float | None = None,
) -> int:
    """Run persistently until stop_event is set; return jobs handled.

    Slots stop claiming once stop is set and in-flight jobs finish
    within shutdown_timeout_seconds. The worker heartbeat keeps
    last_seen_at fresh and reports the shared active count, reset to
    0 on clean shutdown.
    """
    if concurrency < 1:
        raise ValueError("worker concurrency must be at least 1")
    stop = stop_event or threading.Event()
    job_heartbeat = heartbeat_interval_seconds
    if job_heartbeat is None:
        job_heartbeat = heartbeat_seconds
    lock = threading.Lock()
    active = [0]
    handled = [0]

    def heartbeat_loop() -> None:
        _upsert_worker_heartbeat(session_factory, worker_id, 0)
        while not stop.wait(heartbeat_seconds):
            with lock:
                current = active[0]
            try:
                _upsert_worker_heartbeat(session_factory, worker_id, current)
            except Exception:
                pass

    def slot() -> None:
        consecutive_errors = 0
        while not stop.is_set():
            session = session_factory()
            try:
                with lock:
                    active[0] += 1
                try:
                    did = run_once(session, worker_id, clock, crm, scheduler,
                                   heartbeat_interval_seconds=job_heartbeat)
                finally:
                    with lock:
                        active[0] -= 1
            except Exception as err:
                # Claim/DB-level isolation: log and keep looping with
                # bounded exponential backoff, never a busy spin.
                log_event("worker.slot_error", worker_id=str(worker_id),
                          error_code=type(err).__name__)
                delay = min(error_backoff_seconds * (2 ** consecutive_errors), 30.0)
                consecutive_errors += 1
                stop.wait(delay)
                continue
            finally:
                session.close()
            consecutive_errors = 0
            if did:
                with lock:
                    handled[0] += 1
                continue
            stop.wait(_idle_sleep_seconds(session_factory, clock, idle_poll_seconds))

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True, name="worker-heartbeat")
    heartbeat_thread.start()
    slots = [
        threading.Thread(target=slot, daemon=True, name=f"worker-slot-{i}")
        for i in range(concurrency)
    ]
    for thread in slots:
        thread.start()
    log_event("worker.started", worker_id=str(worker_id), concurrency=concurrency)
    try:
        stop_deadline: float | None = None
        while any(thread.is_alive() for thread in slots):
            for thread in slots:
                thread.join(timeout=0.1)
            if stop.is_set():
                if stop_deadline is None:
                    stop_deadline = time.monotonic() + shutdown_timeout_seconds
                if time.monotonic() >= stop_deadline:
                    break
    finally:
        stop.set()
        heartbeat_thread.join(timeout=heartbeat_seconds + 2)
        alive = any(thread.is_alive() for thread in slots)
        if alive:
            log_event("worker.shutdown_timeout", worker_id=str(worker_id))
        else:
            try:
                _upsert_worker_heartbeat(session_factory, worker_id, 0)
            except Exception:
                pass
        log_event("worker.stopped", worker_id=str(worker_id),
                  handled=handled[0], clean=not alive)
    return handled[0]


def _resolve_crm(settings: Settings | None = None) -> CrmPort:
    """Resolve the CRM port from settings; never a test fake."""
    from app.adapters.crm_http import HttpCrmAdapter

    settings = settings or Settings.from_env()
    if settings.crm_mode == "http":
        return HttpCrmAdapter(settings.crm_base_url, settings.crm_token)
    return HttpCrmAdapter(
        settings.crm_base_url or "http://127.0.0.1:8091",
        settings.crm_token or "sim-token",
    )


def main() -> None:
    """Console-script entrypoint: persistent worker until signalled."""
    configure_logging()
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    crm = _resolve_crm(settings)
    stop_event = threading.Event()

    def _signal_handler(signum, frame):  # pragma: no cover - process level
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            pass
    worker_id = uuid.uuid4()
    try:
        run_forever(
            session_factory, worker_id, SystemBusinessClock(), crm,
            concurrency=settings.worker_concurrency,
            idle_poll_seconds=settings.worker_idle_poll_seconds,
            heartbeat_seconds=settings.worker_heartbeat_seconds,
            shutdown_timeout_seconds=settings.worker_shutdown_timeout_seconds,
            error_backoff_seconds=settings.worker_error_backoff_seconds,
            stop_event=stop_event,
        )
    finally:
        close = getattr(crm, "close", None)
        if close is not None:
            close()
        engine.dispose()
        log_event("worker.shutdown_complete", worker_id=str(worker_id))
