"""Worker entrypoint: claim and handle jobs one at a time.

run_once is the single-step primitive the tests drive; run_until_idle
loops it with a fresh session per job; main is the console-script
entrypoint. main resolves the HTTP CRM adapter via _resolve_crm
and runs run_until_idle once; it is not a persistent loop (PR 3).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.db import job_repo
from app.db.session import make_engine, make_session_factory
from app.logging import configure_logging, log_event
from app.ports.clock import BusinessClock
from app.ports.crm import CrmPort, ProviderError
from app.ports.scheduler import Scheduler
from app.services.clock import SystemBusinessClock
from app.settings import Settings
from app.worker.claiming import claim_next, handle_claim


def run_once(
    session: Session,
    worker_id: uuid.UUID,
    clock: BusinessClock,
    crm: CrmPort,
    scheduler: Scheduler | None = None,
) -> bool:
    """Claim and handle at most one due job.

    Returns True when a job was handled and False when nothing
    was due.
    """
    claim = claim_next(session, worker_id, clock)
    if claim is None:
        return False
    try:
        handle_claim(session, claim, clock, crm=crm, scheduler=scheduler)
    except Exception as err:
        # Job-level isolation lives here, not in run_until_idle:
        # the claim is the only place job_id and attempt_no are
        # known. Identifier only: no message, payload or contact
        # data. The job was claimed and attempted, so return True
        # and let the loop continue; no job.handled is emitted.
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
    # Identifiers and outcome only: no contact or CRM field values.
    # The read opens a transaction on the caller's session, so roll
    # it back: the next claim_next on this session must find no
    # transaction already begun.
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
) -> int:
    """Run jobs until none is due; return how many were handled.

    One fresh session per iteration, so a failed job cannot poison
    the next claim. The loop stops when the queue is idle, and
    max_iterations bounds it when given: it is never an unbounded
    loop.
    """
    handled = 0
    while max_iterations is None or handled < max_iterations:
        session = session_factory()
        try:
            if not run_once(session, worker_id, clock, crm, scheduler):
                break
        finally:
            session.close()
        handled += 1
    return handled


def _resolve_crm(settings: Settings | None = None) -> CrmPort:
    """Resolve the CRM port from settings; never a test fake.

    http: explicit CRM_BASE_URL/CRM_TOKEN (Settings already refuses
    http mode without them). mock: the local provider simulator at
    CRM_BASE_URL or its default loopback address. Both return the
    real HTTP adapter.
    """
    from app.adapters.crm_http import HttpCrmAdapter

    settings = settings or Settings.from_env()
    if settings.crm_mode == "http":
        return HttpCrmAdapter(settings.crm_base_url, settings.crm_token)
    return HttpCrmAdapter(
        settings.crm_base_url or "http://127.0.0.1:8091",
        settings.crm_token or "sim-token",
    )


def main() -> None:
    """Console-script entrypoint: one run_until_idle pass."""
    configure_logging()
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    crm = _resolve_crm(settings)
    try:
        run_until_idle(
            session_factory, uuid.uuid4(), SystemBusinessClock(), crm
        )
    finally:
        close = getattr(crm, "close", None)
        if close is not None:
            close()
