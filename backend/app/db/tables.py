"""Core table definitions for the intake write path.

All tables live in the leadops schema, qualified explicitly here so
no connection has to rely on the migration's SET search_path. Column
sets cover the columns the intake path reads or writes; database
defaults fill the rest.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
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
    Column("display_no", Integer),
    Column("contact_id", Uuid, nullable=False),
    Column("required_contact_version", Integer, nullable=False),
    Column("scenario_run_id", Uuid),
    Column("resolution_note", String(500)),
    Column("source", String(40), nullable=False),
    Column("service_type", Text, nullable=False),
    Column("postcode", String(4)),
    Column("message", Text, nullable=False),
    Column("preferred_contact", Text, nullable=False),
    Column("owner_id", Uuid),
    Column("routing_reason", Text),
    Column("rules_version", String(80), nullable=False),
    Column("rules_hash", String(64), nullable=False),
    Column("local_prepared_at", DateTime(timezone=True)),
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
    Column("created_at", DateTime(timezone=True), nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("kind", Text, nullable=False),
    Column("entity_id", Uuid, nullable=False),
    Column("dedupe_key", String(256), nullable=False),
    Column("state", Text, nullable=False),
    Column("attempt_count", Integer, nullable=False),
    Column("cycle_no", Integer, nullable=False),
    Column("cycle_attempt_count", Integer, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("lease_token", Uuid),
    Column("lease_until", DateTime(timezone=True)),
    Column("last_error_code", Text),
    Column("heartbeat_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

external_operations = Table(
    "external_operations",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("job_id", Uuid, nullable=False),
    Column("operation_key", String(500), nullable=False),
    Column("provider", Text, nullable=False),
    Column("verb", String(20), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("status", Text, nullable=False),
    Column("remote_id", String(128)),
    Column("attempt_count", Integer, nullable=False),
    Column("last_checked_at", DateTime(timezone=True)),
)

crm_links = Table(
    "crm_links",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("contact_id", Uuid, nullable=False),
    Column("provider", Text, nullable=False),
    Column("remote_id", String(128)),
    Column("sync_status", Text, nullable=False),
    Column("synced_contact_version", Integer, nullable=False),
    Column("last_synced_at", DateTime(timezone=True)),
    Column("last_error_code", Text),
    Column("version", Integer, nullable=False),
)

job_attempts = Table(
    "job_attempts",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("job_id", Uuid, nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("cycle_no", Integer, nullable=False),
    Column("lease_token", Uuid, nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("outcome", String(80)),
    Column("error_code", Text),
)

operators = Table(
    "operators",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("username", String(80), nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("active", Boolean, nullable=False),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("token_hash", String(64), nullable=False),
    Column("operator_id", Uuid, nullable=False),
    Column("csrf_hash", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
)

integration_health = Table(
    "integration_health",
    metadata,
    Column("provider", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("incident_id", Uuid),
    Column("last_success_at", DateTime(timezone=True)),
    Column("last_error_code", Text),
    Column("blocked_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

activity_events = Table(
    "activity_events",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("enquiry_id", Uuid),
    Column("job_id", Uuid),
    Column("event_type", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("actor_type", Text, nullable=False),
    Column("actor_id", Uuid),
    Column("details_json", JSONB, nullable=False),
    Column("event_seq", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("contact_id", Uuid),
    Column("scenario_run_id", Uuid),
)

worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("worker_id", Uuid, primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("active_job_count", Integer, nullable=False),
)

rate_limit_counters = Table(
    "rate_limit_counters",
    metadata,
    Column("scope", String(40), primary_key=True),
    Column("subject_hash", String(64), primary_key=True),
    Column("window_start", DateTime(timezone=True), primary_key=True),
    Column("count", Integer, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

enquiry_drafts = Table(
    "enquiry_drafts",
    metadata,
    Column("enquiry_id", Uuid, primary_key=True),
    Column("subject", String(200), nullable=False),
    Column("body", String(3000), nullable=False),
    Column("draft_version", Integer, nullable=False),
    Column("updated_by", Uuid),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

draft_edits = Table(
    "draft_edits",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("enquiry_id", Uuid, nullable=False),
    Column("draft_version", Integer, nullable=False),
    Column("subject", String(200), nullable=False),
    Column("body", String(3000), nullable=False),
    Column("operator_id", Uuid),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
