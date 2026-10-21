"""Core table definitions for the intake write path.

All tables live in the leadops schema, qualified explicitly here so
no connection has to rely on the migration's SET search_path. Column
sets cover the columns the intake path reads or writes; database
defaults fill the rest.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="leadops")

owners = Table(
    "owners",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("code", String(40), nullable=False),
    Column("display_name", String(80), nullable=False),
)

contacts = Table(
    "contacts",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("email_norm", String(254), nullable=False),
    Column("first_name", String(80), nullable=False),
    Column("last_name", String(80)),
    Column("phone_e164", String(40)),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

enquiries = Table(
    "enquiries",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("contact_id", Uuid, nullable=False),
    Column("required_contact_version", Integer, nullable=False),
    Column("source", String(40), nullable=False),
    Column("service_type", Text, nullable=False),
    Column("postcode", String(4)),
    Column("message", Text, nullable=False),
    Column("preferred_contact", Text, nullable=False),
    Column("owner_id", Uuid),
    Column("routing_reason", Text),
    Column("rules_version", String(80), nullable=False),
    Column("rules_hash", String(64), nullable=False),
    Column("requires_review", Boolean, nullable=False),
    Column("review_reason_codes", JSONB, nullable=False),
    Column("review_resolved_at", DateTime(timezone=True)),
    Column("review_resolved_by", Uuid),
    Column("version", Integer, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)

ingestion_receipts = Table(
    "ingestion_receipts",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("source", String(40), nullable=False),
    Column("event_key", Uuid, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("content_fingerprint", String(64), nullable=False),
    Column("payload_json", JSONB, nullable=False),
    Column("enquiry_id", Uuid, nullable=False),
    Column("deduplication", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
)

follow_up_tasks = Table(
    "follow_up_tasks",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("enquiry_id", Uuid, nullable=False),
    Column("kind", Text, nullable=False),
    Column("title", String(200), nullable=False),
    Column("owner_id", Uuid, nullable=False),
    Column("status", Text, nullable=False),
    Column("due_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
    Column("cancelled_at", DateTime(timezone=True)),
    Column("action_reason", String(500)),
    Column("version", Integer, nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("kind", Text, nullable=False),
    Column("entity_id", Uuid, nullable=False),
    Column("dedupe_key", String(256), nullable=False),
    Column("state", Text, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
)

activity_events = Table(
    "activity_events",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("enquiry_id", Uuid),
    Column("event_type", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("actor_type", Text, nullable=False),
    Column("actor_id", Uuid),
    Column("details_json", JSONB, nullable=False),
)
