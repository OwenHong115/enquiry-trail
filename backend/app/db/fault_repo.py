"""Repository for scenario runs, demo faults and fault claims (P6)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import case, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
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


def expire_faults(session: Session, run_id: uuid.UUID | None = None) -> int:
    """Transition armed faults past expiry to 'expired' (not just filtered)."""
    now = datetime.now(timezone.utc)
    stmt = update(tables.demo_faults).where(
        tables.demo_faults.c.status == "armed",
        tables.demo_faults.c.expires_at <= now)
    if run_id is not None:
        stmt = stmt.where(tables.demo_faults.c.scenario_run_id == run_id)
    return session.execute(stmt.values(status="expired")).rowcount or 0


def find_armed_fault(session: Session, run_id: uuid.UUID, provider: str):
    """Oldest armed, unexpired fault for (run, provider), row-locked."""
    now = datetime.now(timezone.utc)
    expire_faults(session, run_id)
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

    Returns (mode, consumed_now). The claim row is inserted with
    ON CONFLICT DO NOTHING on UNIQUE(scenario_run_id, claim_identity)
    (a single statement, never check-then-act), and the fault use is
    taken by a guarded decrement (remaining_uses > 0 AND status =
    'armed', checked by rowcount). A caller that loses the race gets
    the cached replay result, never an IntegrityError.
    """
    mode = fault["mode"]
    claim_id = uuid.uuid4()
    claim_stmt = pg_insert(tables.fault_claims).values(
        id=claim_id, match_request_id=uuid.uuid4(), fault_id=fault["id"],
        scenario_run_id=run_id, claim_identity=claim_identity,
        result_json={"mode": mode},
    ).on_conflict_do_nothing(
        index_elements=["scenario_run_id", "claim_identity"],
    ).returning(tables.fault_claims.c.id)
    try:
        with session.begin_nested():
            inserted_id = session.execute(claim_stmt).scalar_one_or_none()
    except IntegrityError:
        inserted_id = None
    if inserted_id is None:
        existing = session.execute(select(tables.fault_claims).where(
            tables.fault_claims.c.scenario_run_id == run_id,
            tables.fault_claims.c.claim_identity == claim_identity)).mappings().first()
        if existing is None:
            return None, False
        return existing["result_json"].get("mode"), False
    decrement = update(tables.demo_faults).where(
        tables.demo_faults.c.id == fault["id"],
        tables.demo_faults.c.remaining_uses > 0,
        tables.demo_faults.c.status == "armed",
    ).values(
        remaining_uses=tables.demo_faults.c.remaining_uses - 1,
        status=case(
            (tables.demo_faults.c.remaining_uses - 1 <= 0, "exhausted"),
            else_="armed",
        ),
    )
    with session.begin_nested():
        consumed_rows = session.execute(decrement).rowcount or 0
    if consumed_rows == 0:
        # The fault was exhausted or expired concurrently: undo the
        # claim so no use and no event are recorded for it.
        with session.begin_nested():
            session.execute(
                tables.fault_claims.delete().where(
                    tables.fault_claims.c.id == claim_id))
        return None, False
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


def evaluate_assertions(session: Session, run_id: uuid.UUID) -> list[dict]:
    """Evaluate the expectations recorded by a run from persisted facts.

    Documented semantics (the smallest honest set): the fault must
    have been triggered exactly when a claim was consumed; every
    enquiry linked to the run must have exactly one follow-up task;
    linked remote contacts must not outnumber linked enquiries.
    """
    run = get_run(session, run_id)
    claims = list_claims(session, run_id)
    enquiry_ids = list(session.execute(select(tables.enquiries.c.id).where(
        tables.enquiries.c.scenario_run_id == run_id)).scalars().all())
    if enquiry_ids:
        task_count = session.execute(select(func.count()).select_from(
            tables.follow_up_tasks).where(
            tables.follow_up_tasks.c.enquiry_id.in_(enquiry_ids))).scalar_one()
        contact_ids = list(session.execute(select(
            tables.enquiries.c.contact_id).where(
            tables.enquiries.c.scenario_run_id == run_id)).scalars().all())
        remote_count = session.execute(select(func.count()).select_from(
            tables.crm_links).where(
            tables.crm_links.c.contact_id.in_(contact_ids))).scalar_one() \
            if contact_ids else 0
    else:
        task_count = 0
        remote_count = 0
    triggered = bool(run["fault_triggered"]) if run is not None else False
    return [
        {"name": "fault_triggered", "expected": len(claims) > 0,
         "actual": triggered, "passed": triggered == (len(claims) > 0)},
        {"name": "follow_up_task_count", "expected": len(enquiry_ids),
         "actual": task_count, "passed": task_count == len(enquiry_ids)},
        {"name": "remote_contact_count_at_most_enquiries",
         "expected_max": len(enquiry_ids), "actual": remote_count,
         "passed": remote_count <= len(enquiry_ids)},
    ]


def finish_run(session: Session, run_id: uuid.UUID):
    """Finish a run: expire faults, evaluate assertions, stamp the end.

    Status becomes 'passed' when every assertion passes and 'failed'
    otherwise; an 'inconclusive' run keeps that status. finished_at
    is always set. Runs inside the caller's transaction.
    """
    run = get_run(session, run_id)
    if run is None:
        return None
    expire_faults(session, run_id)
    results = evaluate_assertions(session, run_id)
    if run["status"] == "inconclusive":
        status = "inconclusive"
    else:
        status = "passed" if all(r["passed"] for r in results) else "failed"
    session.execute(update(tables.scenario_runs).where(
        tables.scenario_runs.c.id == run_id).values(
        assertion_results=results, status=status,
        finished_at=datetime.now(timezone.utc)))
    insert_event(session, run_id=run_id, event_type="scenario.finished",
                 details={"status": status})
    return get_run(session, run_id)


def insert_event(session: Session, *, run_id: uuid.UUID, event_type: str,
                 details: dict, enquiry_id=None, job_id=None) -> None:
    session.execute(insert(tables.activity_events).values(
        id=uuid.uuid4(), enquiry_id=enquiry_id, job_id=job_id,
        scenario_run_id=run_id, event_type=event_type, severity="warning",
        actor_type="demo", actor_id=None, details_json=details))
