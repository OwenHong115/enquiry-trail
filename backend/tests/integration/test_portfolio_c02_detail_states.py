"""Portfolio C02: six enquiry detail states against the OpenAPI schema.

Each case drives one synthetic enquiry to a representative state
(portfolio table in docs/development-plan.md, 2026-11-04, all with
no pending human review), reads the public detail JSON, and
validates it against the exported schema for
GET /api/v1/enquiries/{enquiry_id} with local references resolved:
required fields, nullability, types and published enums. Expected
state combinations are asserted on top of schema validity.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.bootstrap import create_app
from app.ports.crm import ProviderError
from app.settings import Settings
from app.worker.claiming import claim_next, handle_claim
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from support.fake_scheduler import FakeScheduler

CSRF_HEADER = "X-CSRF-Token"
KEY_HEADER = "Idempotency-Key"
WORKER = uuid.UUID("12121212-1212-1212-1212-121212121212")

ALEX: dict = {
    "first_name": "Alex",
    "last_name": "Morgan",
    "email": "alex.demo@example.com",
    "phone": "+61412345678",
    "postcode": "2150",
    "service_type": "bathroom",
    "message": "I need a bathroom renovation next month. My budget is around AUD 20,000.",
    "preferred_contact": "email",
    "acknowledge_demo": True,
}


# --- Minimal validator over the exported schema (local refs only).

def _resolve(schema: dict, node: dict) -> dict:
    while "$ref" in node:
        prefix = "#/components/schemas/"
        ref = node["$ref"]
        assert ref.startswith(prefix), ref
        node = schema["components"]["schemas"][ref[len(prefix):]]
    return node


def _check(schema: dict, node: dict, value: object, path: str) -> list[str]:
    node = _resolve(schema, node)
    if "anyOf" in node:
        errors: list[str] = []
        for branch in node["anyOf"]:
            branch_errors = _check(schema, branch, value, path)
            if not branch_errors:
                return []
            errors.extend(branch_errors)
        return [f"{path}: matched no anyOf branch ({'; '.join(errors)})"]
    if "allOf" in node:
        errors = []
        for branch in node["allOf"]:
            errors.extend(_check(schema, branch, value, path))
        return errors
    errors = []
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: {value!r} not in enum {node['enum']}")
        return errors
    expected = node.get("type")
    if expected == "null":
        if value is not None:
            errors.append(f"{path}: expected null, got {type(value).__name__}")
        return errors
    if value is None:
        errors.append(f"{path}: null not allowed here")
        return errors
    if expected == "string" and not isinstance(value, str):
        errors.append(f"{path}: expected string")
    elif expected == "integer" and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        errors.append(f"{path}: expected integer")
    elif expected == "number" and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
    ):
        errors.append(f"{path}: expected number")
    elif expected == "boolean" and not isinstance(value, bool):
        errors.append(f"{path}: expected boolean")
    elif expected == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected array")
        elif "items" in node:
            for index, item in enumerate(value):
                errors.extend(_check(schema, node["items"], item, f"{path}[{index}]"))
    elif expected == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object")
        else:
            for field in node.get("required", []):
                if field not in value:
                    errors.append(f"{path}: missing required field {field!r}")
            for field, subschema in node.get("properties", {}).items():
                if field in value:
                    errors.extend(
                        _check(schema, subschema, value[field], f"{path}.{field}")
                    )
    return errors


def _detail_schema() -> dict:
    settings = Settings(
        database_url="postgresql+psycopg://offline:offline@127.0.0.1:1/offline",
        session_secret="offline-export-secret",
    )
    return create_app(settings).openapi()


def _validate_detail(body: dict) -> None:
    schema = _detail_schema()
    operation = schema["paths"]["/api/v1/enquiries/{enquiry_id}"]["get"]
    node = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert _check(schema, node, body, "$") == []


# --- Drivers (existing harness patterns).

def _submit(api_client: TestClient) -> dict:
    response = api_client.post(
        "/api/v1/intake/form",
        json=ALEX,
        headers={
            KEY_HEADER: str(uuid.uuid4()),
            CSRF_HEADER: api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def _run_next(postgres_db: str, clock: FakeClock, fake: FakeCrm) -> None:
    factory = sessionmaker(bind=create_engine(postgres_db))
    session = factory()
    try:
        claim = claim_next(session, WORKER, clock)
        assert claim is not None
        handle_claim(session, claim, clock, crm=fake, scheduler=FakeScheduler())
    finally:
        session.close()


def _detail(api_client: TestClient, receipt: dict) -> dict:
    response = api_client.get(receipt["status_url"])
    assert response.status_code == 200, response.text
    return response.json()


def _assert_combo(body: dict, summary: str, attention: str, crm_status: str) -> None:
    _validate_detail(body)
    assert body["requires_review"] is False
    assert body["processing_summary"] == summary
    assert body["attention"] == attention
    assert body["crm"]["status"] == crm_status


def test_processing_immediately_after_intake(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    body = _detail(api_client, receipt)
    _assert_combo(body, "processing", "none", "pending")
    # Current intake behaviour: owner and follow-up task already
    # exist inside the intake transaction.
    assert body["owner"] is not None
    assert body["task"] is not None


def test_ready_after_prepare_and_sync(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    clock = FakeClock(datetime.now(timezone.utc))
    _run_next(postgres_db, clock, FakeCrm())
    _run_next(postgres_db, clock, FakeCrm())
    body = _detail(api_client, receipt)
    _assert_combo(body, "ready", "none", "synced")
    assert body["crm"]["remote_id"]


def test_waiting_retry_after_retryable_failure(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    clock = FakeClock(datetime.now(timezone.utc))
    _run_next(postgres_db, clock, FakeCrm())
    _run_next(
        postgres_db,
        clock,
        FakeCrm(lookup_script=[ProviderError("unavailable", http_status=503)]),
    )
    body = _detail(api_client, receipt)
    _assert_combo(body, "waiting_retry", "retry_scheduled", "waiting_retry")
    assert body["crm"]["next_attempt_at"] is not None


def test_blocked_after_auth_failure(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    clock = FakeClock(datetime.now(timezone.utc))
    _run_next(postgres_db, clock, FakeCrm())
    _run_next(
        postgres_db,
        clock,
        FakeCrm(lookup_script=[ProviderError("auth_failed", http_status=401)]),
    )
    body = _detail(api_client, receipt)
    _assert_combo(body, "blocked", "integration_blocked", "blocked")


def test_reconciling_after_unknown_write(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    clock = FakeClock(datetime.now(timezone.utc))
    _run_next(postgres_db, clock, FakeCrm())
    _run_next(
        postgres_db,
        clock,
        FakeCrm(create_commit_then_error=ProviderError("result_unknown")),
    )
    body = _detail(api_client, receipt)
    _assert_combo(body, "reconciling", "none", "reconciling")


def test_failed_after_terminal_sync_failure(
    api_client: TestClient, postgres_db: str
) -> None:
    receipt = _submit(api_client)
    clock = FakeClock(datetime.now(timezone.utc))
    _run_next(postgres_db, clock, FakeCrm())
    _run_next(
        postgres_db,
        clock,
        FakeCrm(create_error=ProviderError("validation_failed")),
    )
    body = _detail(api_client, receipt)
    _assert_combo(body, "failed", "needs_intervention", "failed")
