"""Extraction during prepare: persistence, fallback, idempotency, detail."""

from __future__ import annotations

import uuid

import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.enquiries import build_enquiry_detail
from app.ports.extraction import ExtractionError, ExtractionResult
from app.services.clock import SystemBusinessClock
from app.services.extraction import ExtractionProfile
from app.services.intake import submit_intake
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_extractor import FakeExtractor
from support.routing_config import ROUTING_CONFIG

WORKER = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
PAYLOAD = {
    "first_name": "Alex", "last_name": "Morgan",
    "email": "alex.demo@example.com",
    "phone": "+61412345678", "postcode": "2150", "service_type": "bathroom",
    "message": "I need a bathroom renovation next month. My budget is around AUD 20,000.",
    "preferred_contact": "email", "acknowledge_demo": True,
}
PROFILE = ExtractionProfile(
    mode="fixture", provider="fixture", model="fixture-model",
    prompt_version="v1", parameters={"temperature": 0},
)


def _factory(postgres_db):
    return sessionmaker(bind=create_engine(postgres_db))


def _fetch(postgres_db, sql, params=None):
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def _submit(postgres_db, email=None):
    payload = dict(PAYLOAD)
    if email:
        payload["email"] = email
    clock = FakeClock(datetime.now(timezone.utc))
    session = _factory(postgres_db)()
    try:
        return submit_intake(
            session, payload, source="web", event_key=uuid.uuid4(),
            clock=clock, routing_config=ROUTING_CONFIG,
        ).enquiry_id
    finally:
        session.close()


def _prepare(postgres_db, extractor, profile=PROFILE):
    session = _factory(postgres_db)()
    try:
        clock = SystemBusinessClock()
        claim = claim_next(session, WORKER, clock)
        assert claim is not None and claim.kind == "prepare_enquiry"
        handle_claim(
            session, claim, clock, crm=FakeCrm(), extractor=extractor,
            extraction_profile=profile,
        )
    finally:
        session.close()


def test_valid_extraction_persisted_with_snapshot(postgres_db):
    enquiry_id = _submit(postgres_db)
    extractor = FakeExtractor(result=ExtractionResult(
        budget=20000, timeframe="next month",
        evidence="My budget is around AUD 20,000."))
    _prepare(postgres_db, extractor)
    rows = _fetch(postgres_db, "SELECT status, result_json, parameters_snapshot, model, prompt_version FROM leadops.enrichments WHERE enquiry_id=:e", {"e": enquiry_id})
    assert len(rows) == 1
    assert rows[0].status == "fixture"
    assert rows[0].result_json["budget"] == 20000
    assert rows[0].result_json["evidence"] == "My budget is around AUD 20,000."
    assert rows[0].model == "fixture-model"
    assert rows[0].prompt_version == "v1"
    assert rows[0].parameters_snapshot["model"] == "fixture-model"
    assert rows[0].parameters_snapshot["prompt_version"] == "v1"
    assert rows[0].parameters_snapshot["parameters"] == {"temperature": 0}


def test_invalid_budget_and_evidence_dropped_to_null(postgres_db):
    enquiry_id = _submit(postgres_db)
    extractor = FakeExtractor(result=ExtractionResult(
        budget=-100, timeframe="next month", evidence="not in the message"))
    _prepare(postgres_db, extractor)
    rows = _fetch(postgres_db, "SELECT result_json FROM leadops.enrichments WHERE enquiry_id=:e", {"e": enquiry_id})
    assert rows[0].result_json["budget"] is None
    assert rows[0].result_json["evidence"] is None
    assert rows[0].result_json["timeframe"] == "next month"


def test_extractor_failure_falls_back_and_still_prepares(postgres_db):
    enquiry_id = _submit(postgres_db)
    extractor = FakeExtractor(error=ExtractionError("unavailable"))
    _prepare(postgres_db, extractor)
    rows = _fetch(postgres_db, "SELECT status, last_error_code FROM leadops.enrichments WHERE enquiry_id=:e", {"e": enquiry_id})
    assert len(rows) == 1
    assert rows[0].status == "template_fallback"
    assert rows[0].last_error_code == "unavailable"
    enquiry = _fetch(postgres_db, "SELECT local_prepared_at FROM leadops.enquiries WHERE id=:e", {"e": enquiry_id})
    assert enquiry[0].local_prepared_at is not None
    jobs = _fetch(postgres_db, "SELECT kind, state FROM leadops.jobs WHERE entity_id=:e", {"e": enquiry_id})
    kinds = {r.kind: r.state for r in jobs}
    assert kinds.get("prepare_enquiry") == "succeeded"
    assert "sync_contact" in kinds
    tasks = _fetch(postgres_db, "SELECT count(*) AS n FROM leadops.follow_up_tasks WHERE enquiry_id=:e", {"e": enquiry_id})
    assert tasks[0].n == 1


def test_prepare_idempotent_rerun_no_duplicate(postgres_db):
    enquiry_id = _submit(postgres_db)
    extractor = FakeExtractor(result=ExtractionResult(
        budget=20000, timeframe="next month",
        evidence="My budget is around AUD 20,000."))
    _prepare(postgres_db, extractor)
    # Simulate lease-recovery re-run of the service directly.
    session = _factory(postgres_db)()
    try:
        from app.services.extraction import extract_and_persist

        extract_and_persist(session, enquiry_id, extractor, PROFILE)
        session.commit()
        extract_and_persist(session, enquiry_id, extractor, PROFILE)
        session.commit()
    finally:
        session.close()
    rows = _fetch(postgres_db, "SELECT count(*) AS n FROM leadops.enrichments WHERE enquiry_id=:e", {"e": enquiry_id})
    assert rows[0].n == 1


def test_detail_enrichment_populated(postgres_db):
    enquiry_id = _submit(postgres_db)
    extractor = FakeExtractor(result=ExtractionResult(
        budget=20000, timeframe="next month",
        evidence="My budget is around AUD 20,000."))
    _prepare(postgres_db, extractor)
    session = _factory(postgres_db)()
    try:
        detail = build_enquiry_detail(session, enquiry_id)
    finally:
        session.close()
    assert detail is not None
    assert detail.enrichment is not None
    assert detail.enrichment["status"] == "fixture"
    assert detail.enrichment["budget"] == 20000
    assert detail.enrichment["timeframe"] == "next month"
    assert detail.enrichment["evidence"] == "My budget is around AUD 20,000."


# 20-case matrix (A21): (label, extractor result/error, expected result_json, expected status)
_MSG = PAYLOAD["message"]
_CASES = [
    ("valid", ExtractionResult(budget=20000, timeframe="next month", evidence="My budget is around AUD 20,000."), {"budget": 20000, "timeframe": "next month", "evidence": "My budget is around AUD 20,000."}, "fixture"),
    ("currency-string", ExtractionResult(budget="AUD 20,000", timeframe="next month", evidence="My budget is around AUD 20,000."), {"budget": 20000, "timeframe": "next month", "evidence": "My budget is around AUD 20,000."}, "fixture"),
    ("float-budget", ExtractionResult(budget=1500.5, timeframe="next month", evidence=None), {"budget": 1500.5, "timeframe": "next month", "evidence": None}, "fixture"),
    ("non-numeric", ExtractionResult(budget="lots", timeframe="next month", evidence=None), {"budget": None, "timeframe": "next month", "evidence": None}, "fixture"),
    ("negative", ExtractionResult(budget=-100, timeframe="next month", evidence=None), {"budget": None, "timeframe": "next month", "evidence": None}, "fixture"),
    ("bool", ExtractionResult(budget=True, timeframe="next month", evidence=None), {"budget": None, "timeframe": "next month", "evidence": None}, "fixture"),
    ("zero", ExtractionResult(budget=0, timeframe="next month", evidence=None), {"budget": 0, "timeframe": "next month", "evidence": None}, "fixture"),
    ("huge", ExtractionResult(budget=10**15, timeframe="next month", evidence=None), {"budget": 10**15, "timeframe": "next month", "evidence": None}, "fixture"),
    ("missing-all", ExtractionResult(budget=None, timeframe=None, evidence=None), {"budget": None, "timeframe": None, "evidence": None}, "fixture"),
    ("evidence-not-in-message", ExtractionResult(budget=20000, timeframe="next month", evidence="not in the message"), {"budget": 20000, "timeframe": "next month", "evidence": None}, "fixture"),
    ("blank-evidence", ExtractionResult(budget=20000, timeframe="next month", evidence="   "), {"budget": 20000, "timeframe": "next month", "evidence": None}, "fixture"),
    ("blank-timeframe", ExtractionResult(budget=20000, timeframe="   ", evidence=None), {"budget": 20000, "timeframe": None, "evidence": None}, "fixture"),
    ("long-timeframe", ExtractionResult(budget=20000, timeframe="x" * 201, evidence=None), {"budget": 20000, "timeframe": None, "evidence": None}, "fixture"),
    ("timeframe-variant", ExtractionResult(budget=None, timeframe="within 2 weeks", evidence=None), {"budget": None, "timeframe": "within 2 weeks", "evidence": None}, "fixture"),
    ("nan-budget", ExtractionResult(budget=float("nan"), timeframe=None, evidence=None), {"budget": None, "timeframe": None, "evidence": None}, "fixture"),
    ("list-budget", ExtractionResult(budget=[20000], timeframe=None, evidence=None), {"budget": None, "timeframe": None, "evidence": None}, "fixture"),
    ("error-unavailable", ExtractionError("unavailable"), None, "template_fallback"),
    ("error-timeout-like", ExtractionError("connect_failed"), None, "template_fallback"),
    ("generic-error", RuntimeError("boom"), None, "template_fallback"),
    ("invalid-response-type", "not-a-result", None, "template_fallback"),
]


@pytest.mark.parametrize("label,result,expected,status", _CASES, ids=[c[0] for c in _CASES])
def test_extraction_matrix(postgres_db, label, result, expected, status):
    enquiry_id = _submit(postgres_db, email=f"matrix-{label}@example.com")
    if isinstance(result, BaseException):
        extractor = FakeExtractor(error=result)
    elif isinstance(result, ExtractionResult):
        extractor = FakeExtractor(result=result)
    else:
        extractor = FakeExtractor(result=result)  # invalid type triggers fallback
    _prepare(postgres_db, extractor)
    rows = _fetch(postgres_db, "SELECT status, result_json FROM leadops.enrichments WHERE enquiry_id=:e", {"e": enquiry_id})
    assert len(rows) == 1
    assert rows[0].status == status
    if expected is not None:
        assert rows[0].result_json == expected
    else:
        # Fallback is the deterministic template extraction of the message.
        assert rows[0].result_json["budget"] == 20000
        assert rows[0].result_json["evidence"] == "My budget is around AUD 20,000."


def test_worker_template_mode_prepare(postgres_db):
    from app.adapters.extraction_template import TemplateExtractor
    enquiry_id = _submit(postgres_db, email="template-mode@example.com")
    profile = ExtractionProfile(mode="template", provider="template", model="fixture-model", prompt_version="v1", parameters={"temperature": 0})
    _prepare(postgres_db, TemplateExtractor(), profile=profile)
    rows = _fetch(postgres_db, "SELECT status, result_json FROM leadops.enrichments WHERE enquiry_id=:e", {"e": enquiry_id})
    assert rows[0].status == "template_fallback"
    assert rows[0].result_json["budget"] == 20000
