"""Worker entrypoint: claim and handle jobs one at a time.

run_once is the single-step primitive the tests drive; run_until_idle
loops it with a fresh session per job; main is the console-script
entrypoint. There is no HTTP CRM adapter in this build (S9), so main
fails fast instead of starting a worker that would silently skip
sync_contact jobs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.db.session import make_engine, make_session_factory
from app.ports.clock import BusinessClock
from app.ports.crm import CrmPort
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
    handle_claim(session, claim, clock, crm=crm, scheduler=scheduler)
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


def _resolve_crm() -> CrmPort:
    """Resolve the production CRM port.

    No HTTP CRM adapter exists in this build (it arrives in S9),
    and a fake must never stand in for it in production code.
    """
    raise RuntimeError(
        "no CRM adapter is available in this build: the HTTP CRM "
        "adapter is not built yet (S9), so the worker cannot start "
        "without silently skipping sync_contact jobs"
    )


def main() -> None:
    """Console-script entrypoint: fail fast until a CRM adapter exists."""
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    crm = _resolve_crm()
    # Unreachable until _resolve_crm can return a real adapter; the
    # wiring below is the loop main will run once one exists.
    run_until_idle(  # pragma: no cover
        session_factory, uuid.uuid4(), SystemBusinessClock(), crm
    )
