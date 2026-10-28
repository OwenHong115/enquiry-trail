"""Unit tests for the fault matcher (P6, A31). No database required."""

from __future__ import annotations

import uuid

import pytest

from app.worker.faults import FaultMatcher, claim_identity, mode_to_provider_error


def test_claim_identity_is_stable_and_scoped_by_operation():
    job = uuid.uuid4()
    a = claim_identity(job_id=job, provider="hubspot", operation="create_contact")
    b = claim_identity(job_id=job, provider="hubspot", operation="create_contact")
    c = claim_identity(job_id=job, provider="hubspot", operation="lookup_contact")
    assert a == b
    assert a != c
    assert str(job) in a


@pytest.mark.parametrize(
    "mode,code",
    [
        ("http_503_before_write", "unavailable"),
        ("http_401_before_write", "auth_failed"),
        ("http_429_before_write", "rate_limited"),
        ("invalid_ai_response", "invalid_response"),
    ],
)
def test_before_write_modes_map_to_provider_errors(mode, code):
    err = mode_to_provider_error(mode)
    assert err is not None
    assert err.code == code


def test_after_write_modes_have_no_before_write_error():
    assert mode_to_provider_error("drop_success_response") is None
    assert mode_to_provider_error("crash_after_remote_write") is None


def test_replay_returns_cached_mode_without_consuming_again():
    calls = {"consume": 0}

    def reader(session, run_id, provider):
        return {"id": uuid.uuid4(), "mode": "http_503_before_write"}

    def claimer(session, run_id, fault, identity):
        existing = getattr(session, "_claims", {})
        if identity in existing:
            return existing[identity], False
        calls["consume"] += 1
        session._claims = {**existing, identity: "http_503_before_write"}
        return "http_503_before_write", True

    matcher = FaultMatcher(reader=reader, claimer=claimer,
                           on_triggered=lambda *a, **k: None,
                           on_director_unavailable=lambda *a, **k: None)
    from types import SimpleNamespace
    session = SimpleNamespace()
    run = uuid.uuid4()
    job = uuid.uuid4()
    first = matcher.check(session, scenario_run_id=run, job_id=job,
                          provider="hubspot", operation="create_contact")
    second = matcher.check(session, scenario_run_id=run, job_id=job,
                           provider="hubspot", operation="create_contact")
    assert first == "http_503_before_write"
    assert second == "http_503_before_write"
    assert calls["consume"] == 1


def test_fault_tables_mirror_migration_constraints():
    from sqlalchemy import UniqueConstraint

    from app.db import tables

    claim_uniques = {
        tuple(sorted(c.name for c in constraint.columns))
        for constraint in tables.fault_claims.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("claim_identity", "scenario_run_id") in claim_uniques
    assert tables.fault_claims.c.match_request_id.unique is True
    fault_fk_targets = {
        fk.target_fullname for fk in tables.fault_claims.foreign_keys
    }
    assert "leadops.demo_faults.id" in fault_fk_targets
    assert "leadops.scenario_runs.id" in fault_fk_targets
    demo_fk_targets = {
        fk.target_fullname for fk in tables.demo_faults.foreign_keys
    }
    assert "leadops.scenario_runs.id" in demo_fk_targets
    assert "leadops.operators.id" in demo_fk_targets
    check_sql = " ".join(
        str(c.sqltext)
        for t in (tables.scenario_runs, tables.demo_faults)
        for c in t.constraints
        if c.__class__.__name__ == "CheckConstraint"
    )
    assert "inconclusive" in check_sql
    assert "crash_after_remote_write" in check_sql


def test_director_unavailable_never_raises_and_reports_inconclusive():
    seen = {}

    def reader(session, run_id, provider):
        raise RuntimeError("director down")

    def on_unavailable(session, run_id, **kw):
        seen["run"] = run_id

    matcher = FaultMatcher(reader=reader, claimer=lambda *a, **k: (None, False),
                           on_triggered=lambda *a, **k: None,
                           on_director_unavailable=on_unavailable)
    run = uuid.uuid4()
    result = matcher.check(object(), scenario_run_id=run, job_id=uuid.uuid4(),
                           provider="hubspot", operation="create_contact")
    assert result is None
    assert seen["run"] == run
