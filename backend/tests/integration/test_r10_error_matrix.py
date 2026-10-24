"""R10 red tests: the provider error matrix and loop isolation.

ProviderError handling is incomplete and asymmetric today:

- _handle_readback handles only auth_failed and re-raises every
  other ProviderError, so a rate_limited or unavailable readback
  kills the handler.
- _record_provider_failure returns False for any code outside
  RETRYABLE_CODES, so validation_failed, not_found, conflict,
  invalid_response and director_unavailable are re-raised out of
  handle_claim; there is no terminal handling for a permanent
  provider failure.
- run_until_idle has try/finally but no except, so any exception
  from one job ends the whole loop and the process.
- A rejected create leaves its committed operation intent
  unresolved, so the next claim treats it as an unknown result
  and starts reading back instead of terminating.

Policy pinned for green (create path, keyed by err.code):

- connect_failed, unavailable, rate_limited -> waiting_retry
  with backoff or Retry-After, job.retry_scheduled, operation
  "failed".
- result_unknown -> reconciling at +1s, one
  crm.operation_unknown, operation "unknown".
- auth_failed -> blocked, provider blocked, one
  integration.blocked, operation closed as "failed" (a definite
  pre-write rejection; R15).
- validation_failed, not_found, conflict, invalid_response,
  director_unavailable -> terminal: job "dead", exactly one
  job.failed, operation "failed", attempt closed.

Readback path:

- connect_failed, unavailable -> stay "reconciling", reschedule
  the next readback, no raise, operation stays "unknown".
- rate_limited -> stay "reconciling", next_attempt_at = now +
  Retry-After (300s cap), no raise, operation stays "unknown".
- auth_failed -> blocked as today, operation stays unresolved.
- validation_failed, not_found, conflict, invalid_response,
  director_unavailable -> terminal: job "dead", one job.failed,
  operation PRESERVED as "unknown".

Loop count semantics (test 3): run_until_idle returns the number
of jobs it claimed and attempted (one per run_once that claimed a
job), regardless of whether that job succeeded, reconciled, or
died. A job that could not be claimed at all does not count.

No sleeps: every instant comes from the fake clock or SQL.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ports.crm import FoundContact, NotFound, ProviderError
from app.services.intake import submit_intake
from app.worker.claiming import claim_next, handle_claim
from app.worker.runner import run_until_idle
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler
from support.routing_config import ROUTING_CONFIG

SOURCE = "web"
T0 = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
WORKER = uuid.UUID("10101010-1010-1010-1010-101010101010")

EVENT_TYPES = (
    "job.retry_scheduled",
    "crm.operation_unknown",
    "integration.blocked",
    "job.failed",
)


def _payload(email: str) -> dict:
    return {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": email,
        "phone": "+61412345678",
        "postcode": "2150",
        "service_type": "bathroom",
        "message": "I need a bathroom renovation next month, around AUD 20,000.",
        "preferred_contact": "email",
        "acknowledge_demo": True,
    }


def _factory(postgres_db: str) -> sessionmaker:
    return sessionmaker(bind=create_engine(postgres_db))


def _fetch(postgres_db: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _execute(postgres_db: str, sql: str, params: dict | None = None) -> None:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


def _submit(postgres_db: str, clock: FakeClock, email: str) -> uuid.UUID:
    session = _factory(postgres_db)()
    try:
        result = submit_intake(
            session,
            _payload(email),
            source=SOURCE,
            event_key=uuid.uuid4(),
            clock=clock,
            routing_config=ROUTING_CONFIG,
        )
        return result.enquiry_id
    finally:
        session.close()


def _claim(postgres_db: str, clock: FakeClock):
    session = _factory(postgres_db)()
    try:
        return claim_next(session, WORKER, clock)
    finally:
        session.close()


def _handle(postgres_db: str, claim, clock: FakeClock, fake: FakeCrm) -> None:
    session = _factory(postgres_db)()
    try:
        handle_claim(session, claim, clock, crm=fake, scheduler=FakeScheduler())
    finally:
        session.close()


def _seed_sync_jobs(
    postgres_db: str, clock: FakeClock, emails: list[str]
) -> dict[str, uuid.UUID]:
    """Submit enquiries and drain every prepare job.

    Returns {email: sync_job_id}. All enquiries are submitted
    first so the claims below hand out prepare jobs only.
    """
    enquiry_by_email = {
        email: _submit(postgres_db, clock, email) for email in emails
    }
    for _ in emails:
        prepare = _claim(postgres_db, clock)
        assert prepare is not None
        assert prepare.kind == "prepare_enquiry"
        _handle(postgres_db, prepare, clock, FakeCrm())
    jobs = {}
    for email, enquiry_id in enquiry_by_email.items():
        jobs[email] = _fetch(
            postgres_db,
            "SELECT id FROM leadops.jobs WHERE kind = 'sync_contact' "
            "AND entity_id = :enquiry_id",
            {"enquiry_id": enquiry_id},
        )[0].id
    return jobs


def _job_row(postgres_db: str, job_id: uuid.UUID):
    return _fetch(
        postgres_db,
        "SELECT state, last_error_code, next_attempt_at "
        "FROM leadops.jobs WHERE id = :job_id",
        {"job_id": job_id},
    )[0]


def _operation_status(postgres_db: str, job_id: uuid.UUID) -> str:
    rows = _fetch(
        postgres_db,
        "SELECT status FROM leadops.external_operations "
        "WHERE job_id = :job_id",
        {"job_id": job_id},
    )
    assert len(rows) == 1
    return rows[0].status


def _event_counts(postgres_db: str) -> dict[str, int]:
    return {
        event_type: _fetch(
            postgres_db,
            "SELECT count(*) AS n FROM leadops.activity_events "
            "WHERE event_type = :event_type",
            {"event_type": event_type},
        )[0].n
        for event_type in EVENT_TYPES
    }


def _attempt(postgres_db: str, job_id: uuid.UUID, attempt_no: int = 1):
    return _fetch(
        postgres_db,
        "SELECT finished_at, outcome, error_code FROM leadops.job_attempts "
        "WHERE job_id = :job_id AND attempt_no = :attempt_no",
        {"job_id": job_id, "attempt_no": attempt_no},
    )[0]


def _drive_to_reconciling(
    postgres_db: str, clock: FakeClock, job_id: uuid.UUID
) -> None:
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_id
    _handle(
        postgres_db,
        claim,
        clock,
        FakeCrm(
            lookup_script=[NotFound()],
            create_commit_then_error=ProviderError("result_unknown"),
        ),
    )
    job = _job_row(postgres_db, job_id)
    assert job.state == "reconciling"
    assert _operation_status(postgres_db, job_id) == "unknown"


# ----------------------------------------------------------------
# 1. Create-path matrix
# ----------------------------------------------------------------

# code, job state, last_error_code, operation status,
# {event_type: exact count}, attempt outcome
CREATE_CASES = [
    ("connect_failed", "waiting_retry", "connect_failed", "failed",
     {"job.retry_scheduled": 1}, "failed"),
    ("unavailable", "waiting_retry", "unavailable", "failed",
     {"job.retry_scheduled": 1}, "failed"),
    ("rate_limited", "waiting_retry", "rate_limited", "failed",
     {"job.retry_scheduled": 1}, "failed"),
    ("result_unknown", "reconciling", "result_unknown", "unknown",
     {"crm.operation_unknown": 1}, "failed"),
    ("auth_failed", "blocked", "auth_failed", "failed",
     {"integration.blocked": 1}, "blocked"),
    ("validation_failed", "dead", "validation_failed", "failed",
     {"job.failed": 1}, "failed"),
    ("not_found", "dead", "not_found", "failed",
     {"job.failed": 1}, "failed"),
    ("conflict", "dead", "conflict", "failed",
     {"job.failed": 1}, "failed"),
    ("invalid_response", "dead", "invalid_response", "failed",
     {"job.failed": 1}, "failed"),
    ("director_unavailable", "dead", "director_unavailable", "failed",
     {"job.failed": 1}, "failed"),
]


@pytest.mark.parametrize(
    "code,state,error_code,op_status,events,attempt_outcome",
    CREATE_CASES,
    ids=[case[0] for case in CREATE_CASES],
)
def test_create_error_matrix_is_total(
    postgres_db: str,
    code: str,
    state: str,
    error_code: str,
    op_status: str,
    events: dict[str, int],
    attempt_outcome: str,
) -> None:
    clock = FakeClock(T0)
    jobs = _seed_sync_jobs(postgres_db, clock, ["create-matrix@example.com"])
    job_id = jobs["create-matrix@example.com"]

    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_id

    # Must not raise, whatever the code.
    _handle(
        postgres_db,
        claim,
        clock,
        FakeCrm(create_error=ProviderError(code)),
    )

    job = _job_row(postgres_db, job_id)
    assert job.state == state
    assert job.last_error_code == error_code
    assert _operation_status(postgres_db, job_id) == op_status

    counts = _event_counts(postgres_db)
    for event_type in EVENT_TYPES:
        assert counts[event_type] == events.get(event_type, 0), (
            event_type,
            counts,
        )

    attempt = _attempt(postgres_db, job_id)
    assert attempt.finished_at is not None
    assert attempt.outcome == attempt_outcome
    assert attempt.error_code == error_code


# ----------------------------------------------------------------
# 2. Readback-path matrix
# ----------------------------------------------------------------

# code, job state, last_error_code (None = do not pin)
READBACK_CASES = [
    ("rate_limited", "reconciling", None),
    ("unavailable", "reconciling", None),
    ("auth_failed", "blocked", "auth_failed"),
    ("validation_failed", "dead", "validation_failed"),
]


@pytest.mark.parametrize(
    "code,state,error_code",
    READBACK_CASES,
    ids=[case[0] for case in READBACK_CASES],
)
def test_readback_error_matrix(
    postgres_db: str, code: str, state: str, error_code: str | None
) -> None:
    clock = FakeClock(T0)
    jobs = _seed_sync_jobs(postgres_db, clock, ["readback-matrix@example.com"])
    job_id = jobs["readback-matrix@example.com"]
    _drive_to_reconciling(postgres_db, clock, job_id)

    # The readback is due 1s after the unknown result.
    clock.advance_to(_job_row(postgres_db, job_id).next_attempt_at)
    claim_time = clock.now_utc()
    claim = _claim(postgres_db, clock)
    assert claim is not None
    assert claim.job_id == job_id

    if code == "rate_limited":
        readback_error = ProviderError(code, retry_after_seconds=300)
    else:
        readback_error = ProviderError(code)
    fake = FakeCrm(by_email=readback_error)

    # Must not raise, whatever the code.
    _handle(postgres_db, claim, clock, fake)

    job = _job_row(postgres_db, job_id)
    assert job.state == state
    if error_code is not None:
        assert job.last_error_code == error_code
    # The remote state is still unknown: preserved in every cell.
    assert _operation_status(postgres_db, job_id) == "unknown"
    assert fake.calls_of("create_contact") == []

    counts = _event_counts(postgres_db)
    # The setup's single operation_unknown is never duplicated.
    assert counts["crm.operation_unknown"] == 1
    assert counts["job.retry_scheduled"] == 0
    if code == "auth_failed":
        assert counts["integration.blocked"] == 1
        assert counts["job.failed"] == 0
    elif code == "validation_failed":
        assert counts["integration.blocked"] == 0
        assert counts["job.failed"] == 1
    else:
        assert counts["integration.blocked"] == 0
        assert counts["job.failed"] == 0
        # Rescheduled for another readback, never terminal.
        assert job.next_attempt_at > claim_time
    if code == "rate_limited":
        assert job.next_attempt_at == claim_time + timedelta(seconds=300)

    attempt = _attempt(postgres_db, job_id, attempt_no=claim.attempt_no)
    assert attempt.finished_at is not None
    if code == "auth_failed":
        assert attempt.outcome == "blocked"
    if code == "validation_failed":
        assert attempt.outcome == "failed"


# ----------------------------------------------------------------
# 3. The loop contains a permanent provider failure
# ----------------------------------------------------------------


class _LoopCrm(FakeCrm):
    """Per-contact behaviour for the mixed-queue loop test."""

    permanent_email: str = ""
    reconciled_email: str = ""
    reconciled_remote_id: str = "hs-reconciled-1"

    def get_contact_by_email(self, email_norm, context):  # type: ignore[override]
        if email_norm == self.reconciled_email:
            return FoundContact(
                remote_id=self.reconciled_remote_id,
                email=email_norm,
                firstname="Alex",
                lastname="Morgan",
                phone="+61412345678",
            )
        return NotFound()

    def create_contact(self, fields, context):  # type: ignore[override]
        if fields.email == self.permanent_email:
            raise ProviderError("validation_failed")
        return super().create_contact(fields, context)


def test_worker_loop_contains_job_level_failure(postgres_db: str) -> None:
    clock = FakeClock(T0)
    permanent = "loop-permanent@example.com"
    success = "loop-success@example.com"
    reconciling = "loop-reconciling@example.com"
    jobs = _seed_sync_jobs(postgres_db, clock, [permanent, success, reconciling])
    # The reconciling job is claimed first for its pre-drive.
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET next_attempt_at = :when WHERE id = :job_id",
        {"when": T0 - timedelta(seconds=120), "job_id": jobs[reconciling]},
    )
    _drive_to_reconciling(postgres_db, clock, jobs[reconciling])
    # The readback is due at T0+1s; make every job due for the loop.
    clock.advance_to(T0 + timedelta(seconds=1))

    crm = _LoopCrm()
    crm.permanent_email = permanent
    crm.reconciled_email = reconciling

    # Must return without raising, even though one job fails
    # permanently on create.
    handled = run_until_idle(
        _factory(postgres_db), WORKER, clock, crm, FakeScheduler(),
        max_iterations=10,
    )

    # Count semantics: one per job claimed and attempted.
    assert handled == 3
    assert _job_row(postgres_db, jobs[success]).state == "succeeded"
    assert _job_row(postgres_db, jobs[permanent]).state == "dead"
    assert _job_row(postgres_db, jobs[reconciling]).state == "succeeded"
    assert _operation_status(postgres_db, jobs[reconciling]) == "reconciled"


# ----------------------------------------------------------------
# 4. An unexpected exception does not kill the loop
# ----------------------------------------------------------------


def test_unexpected_exception_does_not_kill_the_loop(
    postgres_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock(T0)
    first = "loop-boom-first@example.com"
    second = "loop-boom-second@example.com"
    jobs = _seed_sync_jobs(postgres_db, clock, [first, second])
    # The boom job is claimed first.
    _execute(
        postgres_db,
        "UPDATE leadops.jobs SET next_attempt_at = :when WHERE id = :job_id",
        {"when": T0 - timedelta(seconds=60), "job_id": jobs[first]},
    )

    with caplog.at_level(logging.INFO):
        handled = run_until_idle(
            _factory(postgres_db),
            WORKER,
            clock,
            FakeCrm(error=RuntimeError("boom")),
            FakeScheduler(),
            max_iterations=10,
        )

    # The loop returned instead of dying on the first job: both
    # jobs were claimed and attempted (the count semantics from
    # test 3), and neither succeeded through the raising fake.
    assert handled == 2
    assert _job_row(postgres_db, jobs[first]).state != "succeeded"
    assert _job_row(postgres_db, jobs[second]).state != "succeeded"
    second_attempts = _fetch(
        postgres_db,
        "SELECT count(*) AS n FROM leadops.job_attempts "
        "WHERE job_id = :job_id",
        {"job_id": jobs[second]},
    )[0].n
    assert second_attempts == 1

    parsed = []
    for record in caplog.records:
        try:
            value = json.loads(record.getMessage())
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            parsed.append(value)
    matching = [
        record
        for record in parsed
        if record.get("event") == "job.error"
        and record.get("job_id") == str(jobs[first])
        and record.get("attempt_no") == 1
        and record.get("error_code") == "RuntimeError"
    ]
    assert matching, (
        "no matching structured record with event 'job.error' for "
        f"job {jobs[first]}: parsed records were {parsed!r}"
    )
    # Identifier only: no exception message, payload or contact data.
    assert set(matching[0]) <= {"event", "job_id", "attempt_no", "error_code"}
    assert "boom" not in json.dumps(matching[0])
