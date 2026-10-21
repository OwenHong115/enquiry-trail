"""Provider health: blocking on auth failure and operator resume."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db import health_repo
from app.ports.clock import BusinessClock
from app.ports.crm import CallContext, CrmPort

PROVIDER = "hubspot"
# behavior.yaml worker.http_deadline_seconds.
HTTP_DEADLINE_SECONDS = 15


def is_blocked(session: Session, provider: str = PROVIDER) -> bool:
    row = health_repo.get_health(session, provider)
    return row is not None and row.status == "blocked"


def block_provider(
    session: Session, *, enquiry_id: uuid.UUID, provider: str = PROVIDER
) -> uuid.UUID:
    """Open the provider incident unless one is already open.

    Returns the live incident id. An already-blocked provider keeps
    its incident, its blocked_at and its single blocked event.
    """
    row = health_repo.get_health(session, provider)
    if row is not None and row.status == "blocked":
        return row.incident_id
    incident_id = uuid.uuid4()
    if row is None:
        health_repo.insert_health_blocked(session, provider, incident_id)
    else:
        health_repo.update_health_blocked(session, provider, incident_id)
    health_repo.insert_blocked_event(
        session,
        enquiry_id=enquiry_id,
        provider=provider,
        incident_id=incident_id,
    )
    return incident_id


def resume_provider(
    session: Session,
    provider: str,
    crm: CrmPort,
    clock: BusinessClock,
    *,
    operator_id: uuid.UUID,
    reason: str,
) -> bool:
    """Probe a blocked provider and requeue its jobs when healthy.

    Returns True when the probe passed and the provider resumed,
    False when the probe failed and the provider stays blocked. A
    failed probe is an expected outcome, not an exception; callers
    distinguish the two by this return value.
    """
    with session.begin():
        # No operator job exists to borrow context from, so the
        # probe context names the earliest currently blocked sync
        # job (the work the probe is standing in for), cycle/attempt
        # 1, with no external operation.
        probe_job_id = health_repo.earliest_blocked_sync_job(session)
        context = CallContext(
            scenario_run_id=None,
            job_id=probe_job_id or uuid.UUID(int=0),
            cycle_no=1,
            attempt_no=1,
            external_operation_id=None,
            deadline_seconds=HTTP_DEADLINE_SECONDS,
        )
        result = crm.probe(context)
        if not result.ok:
            return False

        row = health_repo.get_health(session, provider)
        incident_id = row.incident_id if row is not None else None
        health_repo.set_health_ok(session, provider)
        health_repo.requeue_blocked_sync_jobs(
            session, next_attempt_at=clock.now_utc()
        )
        health_repo.insert_resumed_event(
            session,
            provider=provider,
            incident_id=incident_id,
            operator_id=operator_id,
            reason=reason,
        )
        return True
