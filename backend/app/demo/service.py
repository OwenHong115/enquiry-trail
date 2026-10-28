"""Scenario-run service for the controlled failure demonstration (P6)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db import fault_repo

MAX_USES = 3
MAX_EXPIRES_IN_SECONDS = 86400


def create_run(session: Session, *, scenario_id: str) -> dict:
    with session.begin():
        row = fault_repo.create_run(session, scenario_id=scenario_id)
        return _run_out(row, faults=[])


def list_runs(session: Session) -> list[dict]:
    rows = fault_repo.list_runs(session)
    session.rollback()
    return [_run_out(r, faults=None) for r in rows]


def get_run(session: Session, run_id: uuid.UUID) -> dict | None:
    row = fault_repo.get_run(session, run_id)
    if row is None:
        session.rollback()
        return None
    faults = fault_repo.list_faults(session, run_id)
    session.rollback()
    return _run_out(row, faults=faults)


def arm_fault(session: Session, *, run_id: uuid.UUID, provider: str, mode: str,
              uses: int, expires_in_seconds: int, created_by: uuid.UUID) -> dict | None:
    if fault_repo.get_run(session, run_id) is None:
        session.rollback()
        return None
    session.rollback()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    with session.begin():
        row = fault_repo.arm_fault(session, run_id=run_id, provider=provider,
                                   mode=mode, uses=uses, expires_at=expires_at,
                                   created_by=created_by)
        return _fault_out(row)


def _run_out(row, *, faults) -> dict:
    out = {
        "id": row["id"], "scenario_id": row["scenario_id"], "status": row["status"],
        "fault_triggered": row["fault_triggered"],
        "assertion_results": row["assertion_results"] or [],
        "created_at": row["created_at"], "finished_at": row["finished_at"],
    }
    if faults is not None:
        out["faults"] = [_fault_out(f) for f in faults]
    return out


def _fault_out(row) -> dict:
    return {
        "id": row["id"], "scenario_run_id": row["scenario_run_id"],
        "provider": row["provider"], "mode": row["mode"], "status": row["status"],
        "remaining_uses": row["remaining_uses"], "expires_at": row["expires_at"],
        "created_at": row["created_at"],
    }
