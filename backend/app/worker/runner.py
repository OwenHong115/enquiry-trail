"""Worker entrypoint: claim and handle jobs, persistently.

run_once is the single-step primitive the tests drive; run_until_idle
loops it until the queue drains (kept for tests); run_forever is the
persistent runtime main() runs: bounded-concurrency slots with
independent sessions, worker and per-job heartbeats with lease
renewal, idle sleep until the earliest future job, bounded error
backoff, and graceful shutdown on SIGINT/SIGTERM or a stop event.
A slot never dies on a DB error: the whole iteration is isolated,
logged (worker.slot_error) and retried with bounded backoff.
"""

from __future__ import annotations

import signal
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import timezone

from sqlalchemy.orm import Session

from app.db import job_repo, worker_repo
from app.db.session import make_engine, make_session_factory
from app.logging import configure_logging, log_event
from app.ports.clock import BusinessClock
from app.ports.crm import CrmPort, ProviderError
from app.ports.notifications import (
    NotificationContext,
    NotificationPort,
    NotificationSent,
    ReadyNotification,
)
from app.ports.scheduler import Scheduler
from app.services import notifications as notification_service
from app.services.clock import SystemBusinessClock
from app.settings import Settings
from app.ports.extraction import ExtractorPort
from app.services import extraction as extraction_service
from app.worker.claiming import claim_next, handle_claim
# Fault matching is wired by app.worker.claiming, not by this runner.

_LEASE_SECONDS = 60


class WorkerUncleanShutdown(RuntimeError):
    """Slots died unexpectedly or outlived the shutdown timeout."""

    def __init__(self, handled: int) -> None:
        super().__init__("worker shutdown was not clean")
        self.handled = handled


def _renew_loop(bind, claim, interval: float, stop: threading.Event) -> None:
    """Renew one job's lease (DB clock) until told to stop or fenced out.

    Transient DB errors are logged (job.heartbeat_error) and retried on
    the next tick; only a real fence loss (zero rows renewed) stops it.
    """
    while not stop.wait(interval):
        session = Session(bind=bind)
        try:
            with session.begin():
                renewed = job_repo.renew_lease(
                    session, claim.job_id, claim.lease_token, _LEASE_SECONDS
                )
            if renewed == 0:
                log_event("job.heartbeat_fence_lost", job_id=str(claim.job_id),
                          attempt_no=claim.attempt_no)
                return
        except Exception as err:
            try:
                session.rollback()
            except Exception:
                pass
            log_event("job.heartbeat_error", job_id=str(claim.job_id),
                      attempt_no=claim.attempt_no, error_code=type(err).__name__)
        finally:
            session.close()


def run_once(
    session: Session,
    worker_id: uuid.UUID,
    clock: BusinessClock,
    crm: CrmPort,
    scheduler: Scheduler | None = None,
    heartbeat_interval_seconds: float = 5.0,
    claim_observer: Callable[[bool], None] | None = None,
    extractor: ExtractorPort | None = None,
    extraction_profile: extraction_service.ExtractionProfile | None = None,
    notifications: NotificationPort | None = None,
) -> bool:
    """Claim and handle at most one due job (True when one was handled).

    claim_observer(True/False) brackets actual claimed execution so
    callers can count active jobs, not claim attempts. Known follow-up:
    an unhandled (non-provider) exception from handle_claim is logged
    as job.error but the job stays running until lease expiry; it is
    not rescheduled here because finalization state is uncertain.
    """
    claim = claim_next(session, worker_id, clock)
    if claim is None:
        return False
    if claim_observer is not None:
        claim_observer(True)
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
        handle_claim(session, claim, clock, crm=crm, scheduler=scheduler, extractor=extractor, extraction_profile=extraction_profile, notifications=notifications)
        if notifications is not None and claim.kind == "sync_contact":
            # Separate transaction after the sync committed: a
            # notification enqueue failure never rolls back sync.
            try:
                with session.begin():
                    notification_service.maybe_enqueue_ready_notification(
                        session, claim.entity_id, enabled=True, now=clock.now_utc()
                    )
            except Exception as notify_err:
                try:
                    session.rollback()
                except Exception:
                    pass
                log_event("notification.enqueue_error", job_id=str(claim.job_id), error_code=type(notify_err).__name__)
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
        if claim_observer is not None:
            claim_observer(False)
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
    extractor: ExtractorPort | None = None,
    extraction_profile: extraction_service.ExtractionProfile | None = None,
    notifications: NotificationPort | None = None,
) -> int:
    """Run jobs until none is due; return how many were handled."""
    handled = 0
    while max_iterations is None or handled < max_iterations:
        session = session_factory()
        try:
            if not run_once(session, worker_id, clock, crm, scheduler,
                            heartbeat_interval_seconds=heartbeat_interval_seconds,
                            extractor=extractor, extraction_profile=extraction_profile,
                            notifications=notifications):
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
    extractor: ExtractorPort | None = None,
    extraction_profile: extraction_service.ExtractionProfile | None = None,
    notifications: NotificationPort | None = None,
) -> int:
    """Run persistently until stop_event is set; return jobs handled.

    Raises WorkerUncleanShutdown (carrying the handled count) when
    slots die unexpectedly or outlive the shutdown timeout; in that
    case the caller's stop_event is not set by us and worker.stopped
    is logged with clean=False.
    """
    if concurrency < 1:
        raise ValueError("worker concurrency must be at least 1")
    stop = stop_event or threading.Event()
    internal_stop = threading.Event()
    job_heartbeat = heartbeat_interval_seconds
    if job_heartbeat is None:
        job_heartbeat = heartbeat_seconds
    lock = threading.Lock()
    active = [0]
    handled = [0]

    def stopping() -> bool:
        return stop.is_set() or internal_stop.is_set()

    def heartbeat_loop() -> None:
        try:
            _upsert_worker_heartbeat(session_factory, worker_id, 0)
        except Exception as err:
            log_event("worker.heartbeat_error", worker_id=str(worker_id),
                      error_code=type(err).__name__)
        while not stopping():
            if stop.wait(heartbeat_seconds):
                break
            if internal_stop.is_set():
                break
            with lock:
                current = active[0]
            try:
                _upsert_worker_heartbeat(session_factory, worker_id, current)
            except Exception as err:
                log_event("worker.heartbeat_error", worker_id=str(worker_id),
                          error_code=type(err).__name__)

    def _set_active(claimed: bool) -> None:
        with lock:
            active[0] += 1 if claimed else -1

    def slot() -> None:
        consecutive_errors = 0
        while not stopping():
            session: Session | None = None
            delay = idle_poll_seconds
            try:
                session = session_factory()
                did = run_once(session, worker_id, clock, crm, scheduler,
                               heartbeat_interval_seconds=job_heartbeat,
                               claim_observer=_set_active,
                               extractor=extractor,
                               extraction_profile=extraction_profile,
                               notifications=notifications)
                if did:
                    with lock:
                        handled[0] += 1
                    consecutive_errors = 0
                    continue
                delay = _idle_sleep_seconds(session_factory, clock, idle_poll_seconds)
            except Exception as err:
                log_event("worker.slot_error", worker_id=str(worker_id),
                          error_code=type(err).__name__)
                delay = min(error_backoff_seconds * (2 ** consecutive_errors), 30.0)
                consecutive_errors += 1
            finally:
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass
            deadline = time.monotonic() + delay
            while not stopping() and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if stop.wait(min(0.1, max(0.0, remaining))):
                    break

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True, name="worker-heartbeat")
    heartbeat_thread.start()
    slots = [
        threading.Thread(target=slot, daemon=True, name=f"worker-slot-{i}")
        for i in range(concurrency)
    ]
    for thread in slots:
        thread.start()
    log_event("worker.started", worker_id=str(worker_id), concurrency=concurrency)
    requested = False
    clean = False
    try:
        stop_deadline: float | None = None
        while any(thread.is_alive() for thread in slots):
            for thread in slots:
                thread.join(timeout=0.1)
            if stop.is_set():
                requested = True
                if stop_deadline is None:
                    stop_deadline = time.monotonic() + shutdown_timeout_seconds
                if time.monotonic() >= stop_deadline:
                    break
    finally:
        internal_stop.set()
        heartbeat_thread.join(timeout=heartbeat_seconds + 2)
        alive = any(thread.is_alive() for thread in slots)
        if alive:
            log_event("worker.shutdown_timeout", worker_id=str(worker_id))
        elif not requested and not stop.is_set():
            log_event("worker.slot_died", worker_id=str(worker_id))
        else:
            try:
                _upsert_worker_heartbeat(session_factory, worker_id, 0)
            except Exception:
                pass
        clean = not alive and (requested or stop.is_set())
        log_event("worker.stopped", worker_id=str(worker_id),
                  handled=handled[0], clean=clean)
    if not clean:
        raise WorkerUncleanShutdown(handled[0])
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


class _ConfiguredChannelNotifications:
    """Notification port that stamps the configured Slack channel.

    The ready message is built without a channel, so the configured
    channel is applied here at the construction seam: an explicit
    per-message channel wins, and an unconfigured channel leaves the
    message unchanged.
    """

    def __init__(self, inner: NotificationPort, channel: str) -> None:
        self._inner = inner
        self._channel = channel

    def send_ready(
        self, message: ReadyNotification, context: NotificationContext
    ) -> NotificationSent:
        if message.channel is None:
            message = replace(message, channel=self._channel)
        return self._inner.send_ready(message, context)

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()


def _resolve_notifications(settings: Settings | None = None):
    """Resolve the notification port; disabled returns None, never a fake."""
    from app.adapters.notifications_sim import HttpNotificationAdapter

    settings = settings or Settings.from_env()
    if settings.slack_mode == "disabled":
        return None
    adapter = HttpNotificationAdapter(settings.slack_base_url, settings.slack_token)
    channel = settings.slack_channel.strip()
    if not channel:
        return adapter
    return _ConfiguredChannelNotifications(adapter, channel)


def _resolve_extractor(settings: Settings | None = None) -> ExtractorPort:
    """Resolve the extractor from settings; connected never falls back."""
    from app.adapters.extraction_fixture import FixtureExtractor
    from app.adapters.extraction_template import TemplateExtractor

    settings = settings or Settings.from_env()
    if settings.extractor_mode == "fixture":
        return FixtureExtractor()
    if settings.extractor_mode == "template":
        return TemplateExtractor()
    raise RuntimeError(
        "connected extraction is gated on the live-extraction finding; "
        "no live extractor is available in this build"
    )


def _extraction_profile(settings: Settings) -> extraction_service.ExtractionProfile:
    provider = {"fixture": "fixture", "template": "template",
                "connected": "connected"}[settings.extractor_mode]
    return extraction_service.ExtractionProfile(
        mode=settings.extractor_mode, provider=provider,
        model=settings.extractor_model,
        prompt_version=settings.extractor_prompt_version,
        parameters={"extractor_mode": settings.extractor_mode, "provider": provider},
    )


def main() -> None:
    """Console-script entrypoint: persistent worker until signalled."""
    configure_logging()
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    crm = _resolve_crm(settings)
    notifications = _resolve_notifications(settings)
    extractor = _resolve_extractor(settings)
    extraction_profile = _extraction_profile(settings)
    stop_event = threading.Event()

    def _signal_handler(signum, frame):  # pragma: no cover - process level
        stop_event.set()

    sigbreak = getattr(signal, "SIGBREAK", None)
    signals = [signal.SIGINT, signal.SIGTERM]
    if sigbreak is not None:
        signals.append(sigbreak)
    for sig in signals:
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            pass
    worker_id = uuid.uuid4()
    clean = True
    try:
        run_forever(
            session_factory, worker_id, SystemBusinessClock(), crm,
            concurrency=settings.worker_concurrency,
            idle_poll_seconds=settings.worker_idle_poll_seconds,
            heartbeat_seconds=settings.worker_heartbeat_seconds,
            shutdown_timeout_seconds=settings.worker_shutdown_timeout_seconds,
            error_backoff_seconds=settings.worker_error_backoff_seconds,
            stop_event=stop_event,
            extractor=extractor, extraction_profile=extraction_profile,
            notifications=notifications,
        )
    except WorkerUncleanShutdown:
        # Slots may still be alive: do not close the adapter or
        # dispose the engine under in-flight work.
        clean = False
    finally:
        if clean:
            close = getattr(crm, "close", None)
            if close is not None:
                close()
            notify_close = getattr(notifications, "close", None)
            if notify_close is not None:
                notify_close()
            engine.dispose()
        log_event("worker.shutdown_complete", worker_id=str(worker_id), clean=clean)
    if not clean:
        # Non-zero so a supervisor / Compose restart policy can act.
        sys.exit(1)
