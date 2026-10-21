"""Harness proof: the migrated test database behaves as the schema says.

These tests verify the harness itself (migration state, tables, one
uniqueness fact), not product behaviour.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

BACKEND_DIR = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "ingestion_receipts",
    "contacts",
    "enquiries",
    "follow_up_tasks",
    "jobs",
}


def test_migration_is_at_head(postgres_db: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
    assert current == head


def test_tables_under_test_exist(postgres_db: str) -> None:
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'leadops'"
                )
            ).scalars()
            found = set(rows)
    finally:
        engine.dispose()
    assert EXPECTED_TABLES <= found


def test_receipt_source_event_key_is_unique(postgres_db: str) -> None:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            contact_id = conn.execute(
                text(
                    "INSERT INTO leadops.contacts (email_norm, first_name) "
                    "VALUES ('harness@example.com', 'Harness') RETURNING id"
                )
            ).scalar_one()
            enquiry_id = conn.execute(
                text(
                    "INSERT INTO leadops.enquiries ("
                    "contact_id, required_contact_version, source, "
                    "service_type, message, preferred_contact, rules_version, "
                    "rules_hash, received_at) VALUES ("
                    ":contact_id, 1, 'web', 'bathroom', "
                    "'Harness enquiry message body.', 'email', 'routing-v1', "
                    ":rules_hash, now()) RETURNING id"
                ),
                {"contact_id": contact_id, "rules_hash": "a" * 64},
            ).scalar_one()
            event_key = uuid.uuid4()
            insert_receipt = text(
                "INSERT INTO leadops.ingestion_receipts ("
                "source, event_key, payload_hash, content_fingerprint, "
                "payload_json, enquiry_id, deduplication, received_at) "
                "VALUES ('web', :event_key, :payload_hash, :fingerprint, "
                "'{}'::jsonb, :enquiry_id, 'new', now())"
            )
            params = {
                "event_key": event_key,
                "payload_hash": "b" * 64,
                "fingerprint": "c" * 64,
                "enquiry_id": enquiry_id,
            }
            conn.execute(insert_receipt, params)
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(insert_receipt, params)
    finally:
        engine.dispose()
