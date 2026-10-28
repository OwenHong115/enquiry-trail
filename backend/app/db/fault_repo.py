"""Repository for scenario runs, demo faults and fault claims (P6)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.db import tables

FAULT_MODES = (
    "http_503_before_write", "http_401_before_write", "http_429_before_write",
    "drop_success_response", "invalid_ai_response", "crash_after_remote_write",
)
FAULT_PROVIDERS = ("hubspot", "slack", "ai")


def create_run(session: Session, *, scenario_id: str):
    run_id = uuid.uuid4()
    session.execute(insert(tables.scenario_runs).values(
        id=run_id, scenario_id=scenario_id, status="pending",
        fault_triggered=False, assertion_results=[]))
    return get_run(session, run_id)


def get_run(session: Session, run_id: uuid.UUID):
    return session.execute(select(tables.scenario_runs).where(
        tables.scenario_runs.c.id == run_id)).mappings().first()


def list_runs(session: Session, *, limit: int = 50):
    return list(session.execute(select(tables.scenario_runs).order_by(
        tables.scenario_runs.c.created_at.desc()).limit(limit)).mappings().all())


def arm_fault(session: Session, *, run_id: uuid.UUID, provider: str, mode: str,
              uses: int, expires_at: datetime, created_by: uuid.UUID):
    fault_id = uuid.uuid4()
    session.execute(insert(tables.demo_faults).values(
        id=fault_id, scenario_run_id=run_id, provider=provider, mode=mode,
        status="armed", remaining_uses=uses, expires_at=expires_at,
        created_by=created_by))
    return get_fault(session, fault_id)


def get_fault(session: Session, fault_id: uuid.UUID):
    return session.execute(select(tables.demo_faults).where(
        tables.demo_faults.c.id == fault_id)).mappings().first()


def list_faults(session: Session, run_id: uuid.UUID):
    return list(session.execute(select(tables.demo_faults).where(
        tables.demo_faults.c.scenario_run_id == run_id).order_by(
        tables.demo_faults.c.created_at.asc())).mappings().all())


def list_claims(session: Session, run_id: uuid.UUID):
    return list(session.execute(select(tables.fault_claims).where(
        tables.fault_claims.c.scenario_run_id == run_id)).mappings().all())


def find_armed_fault(session: Session, run_id: uuid.UUID, provider: str):
    """Oldest armed, unexpired fault for (run, provider), row-locked."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    row = session.execute(
        select(tables.demo_faults).where(
            tables.demo_faults.c.scenario_run_id == run_id,
            tables.demo_faults.c.provider == provider,
            tables.demo_faults.c.status == "armed",
            tables.demo_faults.c.remaining_uses > 0,
            tables.demo_faults.c.expires_at > now,
        ).order_by(tables.demo_faults.c.created_at.asc()).with_for_update()
    ).mappings().first()
    return dict(row) if row is not None else None


def try_claim(session: Session, *, run_id: uuid.UUID, fault: dict, claim_identity: str):
    """Consume one use atomically; replay returns the cached mode.

    Returns (mode, consumed_now). The UNIQUE(scenario_run_id,
    claim_identity) constraint makes a replay return the first
    result without consuming another use.
    """
    existing = session.execute(select(tables.fault_claims).where(
        tables.fault_claims.c.scenario_run_id == run_id,
        tables.fault_claims.c.claim_identity == claim_identity)).mappings().first()
    if existing is not None:
        return existing["result_json"].get("mode"), False
    mode = fault["mode"]
    session.execute(insert(tables.fault_claims).values(
        id=uuid.uuid4(), match_request_id=uuid.uuid4(), fault_id=fault["id"],
        scenario_run_id=run_id, claim_identity=claim_identity,
        result_json={"mode": mode}))
    remaining = fault["remaining_uses"] - 1
    session.execute(update(tables.demo_faults).where(
        tables.demo_faults.c.id == fault["id"]).values(
        remaining_uses=remaining,
        status="exhausted" if remaining <= 0 else "armed"))
    session.execute(update(tables.scenario_runs).where(
        tables.scenario_runs.c.id == run_id).values(
        fault_triggered=True, status="running"))
    insert_event(session, run_id=run_id, event_type="fault.triggered",
                 details={"mode": mode, "fault_id": str(fault["id"]),
                          "claim_identity": claim_identity})
    return mode, True


def mark_inconclusive(session: Session, run_id: uuid.UUID) -> None:
    session.execute(update(tables.scenario_runs).where(
        tables.scenario_runs.c.id == run_id).values(status="inconclusive"))
    insert_event(session, run_id=run_id, event_type="fault.director_unavailable",
                 details={"reason": "matcher_read_failed"})


def insert_event(session: Session, *, run_id: uuid.UUID, event_type: str,
                 details: dict, enquiry_id=None, job_id=None) -> None:
    session.execute(insert(tables.activity_events).values(
        id=uuid.uuid4(), enquiry_id=enquiry_id, job_id=job_id,
        scenario_run_id=run_id, event_type=event_type, severity="warning",
        actor_type="demo", actor_id=None, details_json=details))
