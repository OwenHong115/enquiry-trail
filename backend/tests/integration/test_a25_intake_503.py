"""A25: a database failure during intake must answer 503, atomically.

A transaction/database failure escaping ``submit_intake`` (here
injected at the initial-jobs insert) must map to ``503`` with the
standard error envelope and ``service_unavailable`` code, leave no
half-written intake behind, and allow a retry with the same
Idempotency-Key to create exactly one enquiry. A non-database error
must still surface as ``500 internal_error``.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from app.db import intake_repo
from app.services import intake as intake_service

CSRF_HEADER = "X-CSRF-Token"
KEY_HEADER = "Idempotency-Key"

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

COUNT_TABLES = (
    "ingestion_receipts",
    "contacts",
    "enquiries",
    "jobs",
    "follow_up_tasks",
)


def _submit(api_client: TestClient, payload: dict, key: uuid.UUID):
    return api_client.post(
        "/api/v1/intake/form",
        json=payload,
        headers={
            KEY_HEADER: str(key),
            CSRF_HEADER: api_client.csrf_token,  # type: ignore[attr-defined]
        },
    )


def _counts(postgres_db: str) -> dict[str, int]:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return {
                table: conn.execute(
                    text(f"SELECT count(*) FROM leadops.{table}")
                ).scalar_one()
                for table in COUNT_TABLES
            }
    finally:
        engine.dispose()


def _error(response) -> dict:
    body = response.json()
    assert "error" in body, f"not an Error envelope: {body!r}"
    return body["error"]


def _fail_before_jobs_insert(*args: object, **kwargs: object) -> None:
    raise OperationalError(
        "INSERT INTO leadops.jobs", {}, ConnectionError("database unavailable")
    )


def test_a25_database_failure_is_503_atomic_and_retryable(
    api_client: TestClient,
    postgres_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = uuid.uuid4()
    monkeypatch.setattr(
        intake_repo, "insert_initial_job", _fail_before_jobs_insert
    )

    response = _submit(api_client, ALEX, key)

    assert response.status_code == 503, response.text
    error = _error(response)
    assert error["code"] == "service_unavailable"
    assert error["message"]
    assert error["field_errors"] == []
    assert error["retry_after_seconds"] is None
    request_id = response.headers.get("X-Request-ID")
    assert request_id
    assert error["request_id"] == request_id

    # No half-written receipt, enquiry, contact, job or task.
    assert _counts(postgres_db) == {table: 0 for table in COUNT_TABLES}

    # Retry with the same key and payload now succeeds exactly once.
    monkeypatch.undo()
    retry = _submit(api_client, ALEX, key)
    assert retry.status_code == 202, retry.text
    assert retry.json()["replayed"] is False
    assert _counts(postgres_db) == {table: 1 for table in COUNT_TABLES}


def test_a25_programming_error_still_returns_500(
    api_client: TestClient,
    postgres_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise ProgrammingError(
            "INSERT INTO leadops.jobs", {}, Exception("syntax error")
        )

    monkeypatch.setattr(intake_repo, "insert_initial_job", _fail)

    response = _submit(api_client, ALEX, uuid.uuid4())

    assert response.status_code == 500, response.text
    error = _error(response)
    assert error["code"] == "internal_error"
    assert _counts(postgres_db) == {table: 0 for table in COUNT_TABLES}


def test_a25_non_receipt_integrity_error_still_returns_500(
    api_client: TestClient,
    postgres_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # insert_initial_job is not a receipt insert, so this
    # IntegrityError is not the receipt-race case: it must not
    # become a retryable 503.
    def _fail(*args: object, **kwargs: object) -> None:
        raise IntegrityError(
            "INSERT INTO leadops.jobs", {}, Exception("unique violation")
        )

    monkeypatch.setattr(intake_repo, "insert_initial_job", _fail)

    response = _submit(api_client, ALEX, uuid.uuid4())

    assert response.status_code == 500, response.text
    error = _error(response)
    assert error["code"] == "internal_error"
    assert _counts(postgres_db) == {table: 0 for table in COUNT_TABLES}


def test_a25_non_database_error_still_returns_500(
    api_client: TestClient,
    postgres_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _barrier(*args: object, **kwargs: object) -> None:
        raise RuntimeError("barrier: task insert forced to fail")

    monkeypatch.setattr(intake_service, "create_follow_up_task", _barrier)

    response = _submit(api_client, ALEX, uuid.uuid4())

    assert response.status_code == 500, response.text
    error = _error(response)
    assert error["code"] == "internal_error"
    assert response.headers.get("X-Request-ID")
    assert _counts(postgres_db) == {table: 0 for table in COUNT_TABLES}
