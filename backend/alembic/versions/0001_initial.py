"""Initial schema.

Derived from the schema contract (plan package v3.0, LeadOps v3 target
schema). The DDL below transcribes that contract's tables, columns,
constraints and indexes without renaming, adding or dropping anything.
This migration is the baseline; later migrations are Alembic's authority.
"""

from __future__ import annotations

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_DDL = r"""
CREATE SCHEMA IF NOT EXISTS leadops;

SET search_path TO leadops, public;

CREATE TABLE operators (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username varchar(80) NOT NULL UNIQUE,
  password_hash text NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token_hash char(64) NOT NULL UNIQUE,
  operator_id uuid NOT NULL REFERENCES operators(id),
  csrf_hash char(64) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz
);

CREATE INDEX ix_sessions_expiry ON sessions(expires_at);

CREATE TABLE owners (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code varchar(40) NOT NULL UNIQUE,
  display_name varchar(80) NOT NULL,
  active boolean NOT NULL DEFAULT true
);

CREATE TABLE scenario_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scenario_id varchar(80) NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  fault_triggered boolean NOT NULL DEFAULT false,
  assertion_results jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at timestamptz,
  CONSTRAINT ck_scenario_runs_status CHECK (status IN ('pending', 'running', 'passed', 'failed', 'inconclusive'))
);

CREATE TABLE contacts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email_norm varchar(254) NOT NULL UNIQUE,
  first_name varchar(80) NOT NULL,
  last_name varchar(80),
  phone_e164 varchar(40),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK(version>=1),
  CHECK(email_norm = lower(btrim(email_norm)))
);

CREATE INDEX ix_contacts_phone ON contacts(phone_e164);

CREATE TABLE enquiries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  display_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  contact_id uuid NOT NULL REFERENCES contacts(id),
  required_contact_version integer NOT NULL,
  scenario_run_id uuid REFERENCES scenario_runs(id),
  source varchar(40) NOT NULL,
  service_type text NOT NULL,
  postcode char(4),
  message text NOT NULL,
  preferred_contact text NOT NULL,
  owner_id uuid REFERENCES owners(id),
  routing_reason text,
  rules_version varchar(80) NOT NULL,
  rules_hash char(64) NOT NULL,
  requires_review boolean NOT NULL DEFAULT false,
  review_reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb,
  review_resolved_at timestamptz,
  review_resolved_by uuid REFERENCES operators(id),
  resolution_note varchar(500),
  local_prepared_at timestamptz,
  received_at timestamptz NOT NULL,
  version integer NOT NULL DEFAULT 1,
  CONSTRAINT ck_enquiries_service_type CHECK (service_type IN ('bathroom', 'kitchen', 'general', 'unsure')),
  CONSTRAINT ck_enquiries_preferred_contact CHECK (preferred_contact IN ('email', 'phone')),
  CONSTRAINT ck_enquiries_routing_reason CHECK (routing_reason IN ('missing_routing_information', 'west_renovation', 'inner_city', 'general_enquiry', 'outside_configured_area', 'manual_assignment')),
  CHECK(required_contact_version>=1 AND version>=1),
  CHECK(postcode IS NULL OR postcode ~ '^[0-9]{4}$'),
  CHECK(char_length(message) BETWEEN 20 AND 2000)
);

CREATE INDEX ix_enquiries_contact ON enquiries(contact_id);

CREATE INDEX ix_enquiries_received ON enquiries(received_at DESC,id DESC);

CREATE INDEX ix_enquiries_owner ON enquiries(owner_id);

CREATE INDEX ix_enquiries_run ON enquiries(scenario_run_id);

CREATE TABLE ingestion_receipts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source varchar(40) NOT NULL,
  event_key uuid NOT NULL,
  payload_hash char(64) NOT NULL,
  content_fingerprint char(64) NOT NULL,
  payload_json jsonb NOT NULL,
  enquiry_id uuid NOT NULL REFERENCES enquiries(id),
  deduplication text NOT NULL,
  received_at timestamptz NOT NULL,
  UNIQUE(source,event_key),
  CONSTRAINT ck_ingestion_receipts_deduplication CHECK (deduplication IN ('new', 'content_duplicate'))
);

CREATE INDEX ix_receipts_fingerprint ON ingestion_receipts(source,content_fingerprint,received_at);

CREATE TABLE enrichments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enquiry_id uuid NOT NULL UNIQUE REFERENCES enquiries(id),
  status text NOT NULL DEFAULT 'pending',
  input_hash char(64) NOT NULL,
  provider varchar(80),
  model varchar(120),
  prompt_version varchar(80) NOT NULL,
  parameters_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_json jsonb,
  usage_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_error_code text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT ck_enrichments_status CHECK (status IN ('pending', 'extracted', 'fixture', 'template_fallback')),
  CONSTRAINT ck_enrichments_last_error_code CHECK (last_error_code IN ('connect_failed', 'rate_limited', 'unavailable', 'auth_failed', 'validation_failed', 'not_found', 'conflict', 'result_unknown', 'invalid_response', 'director_unavailable'))
);

CREATE TABLE enquiry_drafts (
  enquiry_id uuid PRIMARY KEY REFERENCES enquiries(id),
  subject varchar(200) NOT NULL,
  body varchar(3000) NOT NULL,
  draft_version integer NOT NULL DEFAULT 1,
  updated_by uuid REFERENCES operators(id),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK(draft_version>=1)
);

CREATE TABLE draft_edits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enquiry_id uuid NOT NULL REFERENCES enquiries(id),
  draft_version integer NOT NULL,
  subject varchar(200) NOT NULL,
  body varchar(3000) NOT NULL,
  operator_id uuid REFERENCES operators(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(enquiry_id,draft_version)
);

CREATE TABLE follow_up_tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enquiry_id uuid NOT NULL REFERENCES enquiries(id),
  kind text NOT NULL DEFAULT 'initial_follow_up',
  title varchar(200) NOT NULL,
  owner_id uuid NOT NULL REFERENCES owners(id),
  status text NOT NULL DEFAULT 'open',
  due_at timestamptz NOT NULL,
  completed_at timestamptz,
  cancelled_at timestamptz,
  action_reason varchar(500),
  version integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(enquiry_id,kind),
  CHECK(kind='initial_follow_up'),
  CONSTRAINT ck_follow_up_tasks_status CHECK (status IN ('open', 'completed', 'cancelled')),
  CHECK((status='open' AND completed_at IS NULL AND cancelled_at IS NULL) OR (status='completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR (status='cancelled' AND cancelled_at IS NOT NULL AND completed_at IS NULL)),
  CHECK(version>=1)
);

CREATE INDEX ix_tasks_due ON follow_up_tasks(status,due_at,id);

CREATE TABLE crm_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_id uuid NOT NULL REFERENCES contacts(id),
  provider text NOT NULL DEFAULT 'hubspot',
  remote_id varchar(128),
  sync_status text NOT NULL DEFAULT 'pending',
  synced_contact_version integer NOT NULL DEFAULT 0,
  last_synced_at timestamptz,
  last_snapshot_json jsonb,
  last_error_code text,
  version integer NOT NULL DEFAULT 1,
  UNIQUE(contact_id,provider),
  CHECK(provider='hubspot'),
  CONSTRAINT ck_crm_links_sync_status CHECK (sync_status IN ('pending', 'syncing', 'waiting_retry', 'reconciling', 'synced', 'blocked', 'failed')),
  CONSTRAINT ck_crm_links_last_error_code CHECK (last_error_code IN ('connect_failed', 'rate_limited', 'unavailable', 'auth_failed', 'validation_failed', 'not_found', 'conflict', 'result_unknown', 'invalid_response', 'director_unavailable')),
  CHECK(synced_contact_version>=0 AND version>=1)
);

CREATE UNIQUE INDEX ux_crm_remote ON crm_links(provider,remote_id) WHERE remote_id IS NOT NULL;

CREATE TABLE jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL,
  entity_id uuid NOT NULL,
  scenario_run_id uuid REFERENCES scenario_runs(id),
  dedupe_key varchar(256) NOT NULL UNIQUE,
  state text NOT NULL DEFAULT 'pending',
  attempt_count integer NOT NULL DEFAULT 0,
  cycle_no integer NOT NULL DEFAULT 1,
  cycle_attempt_count integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL,
  lease_token uuid,
  lease_until timestamptz,
  heartbeat_at timestamptz,
  last_error_code text,
  payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT ck_jobs_kind CHECK (kind IN ('prepare_enquiry', 'sync_contact', 'notify_ready', 'reconcile_contact', 'integration_alert')),
  CONSTRAINT ck_jobs_state CHECK (state IN ('pending', 'running', 'waiting_retry', 'waiting_dependency', 'blocked', 'reconciling', 'succeeded', 'dead')),
  CONSTRAINT ck_jobs_last_error_code CHECK (last_error_code IN ('connect_failed', 'rate_limited', 'unavailable', 'auth_failed', 'validation_failed', 'not_found', 'conflict', 'result_unknown', 'invalid_response', 'director_unavailable')),
  CHECK(attempt_count>=0 AND cycle_no>=1 AND cycle_attempt_count BETWEEN 0 AND 4),
  CHECK(state <> 'running' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL))
);

CREATE INDEX ix_jobs_due ON jobs(state,next_attempt_at,created_at,id);

CREATE INDEX ix_jobs_lease ON jobs(lease_until) WHERE lease_until IS NOT NULL;

CREATE TABLE job_attempts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id uuid NOT NULL REFERENCES jobs(id),
  attempt_no integer NOT NULL,
  cycle_no integer NOT NULL,
  claimed_contact_version integer,
  lease_token uuid NOT NULL,
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  finished_at timestamptz,
  outcome varchar(80),
  error_code text,
  http_status integer,
  provider_request_id varchar(200),
  latency_ms integer,
  UNIQUE(job_id,attempt_no),
  CHECK(attempt_no>=1 AND cycle_no>=1),
  CONSTRAINT ck_job_attempts_error_code CHECK (error_code IN ('connect_failed', 'rate_limited', 'unavailable', 'auth_failed', 'validation_failed', 'not_found', 'conflict', 'result_unknown', 'invalid_response', 'director_unavailable'))
);

CREATE TABLE external_operations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id uuid NOT NULL REFERENCES jobs(id),
  scenario_run_id uuid REFERENCES scenario_runs(id),
  operation_key varchar(500) NOT NULL UNIQUE,
  provider text NOT NULL,
  verb varchar(20) NOT NULL,
  request_hash char(64) NOT NULL,
  status text NOT NULL DEFAULT 'intent',
  remote_id varchar(128),
  attempt_count integer NOT NULL DEFAULT 0,
  last_checked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT ck_external_operations_provider CHECK (provider IN ('hubspot', 'slack', 'ai')),
  CONSTRAINT ck_external_operations_status CHECK (status IN ('intent', 'unknown', 'succeeded', 'reconciled', 'failed')),
  CHECK(attempt_count>=0)
);

CREATE TABLE activity_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enquiry_id uuid REFERENCES enquiries(id),
  contact_id uuid REFERENCES contacts(id),
  job_id uuid REFERENCES jobs(id),
  scenario_run_id uuid REFERENCES scenario_runs(id),
  event_seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  event_type text NOT NULL,
  severity text NOT NULL,
  actor_type text NOT NULL,
  actor_id uuid,
  details_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT ck_activity_events_event_type CHECK (event_type IN ('enquiry.received', 'intake.replayed', 'intake.content_duplicate', 'contact.possible_duplicate', 'enquiry.prepared', 'task.created', 'enrichment.completed', 'enrichment.fallback', 'crm.sync_started', 'crm.field_preserved', 'crm.operation_unknown', 'crm.recovered_existing', 'crm.synced', 'job.retry_scheduled', 'job.failed', 'job.lease_recovered', 'integration.blocked', 'integration.resumed', 'owner.changed', 'review.resolved', 'draft.saved', 'task.completed', 'task.cancelled', 'notification.sent', 'notification.unknown', 'notification.failed', 'fault.triggered', 'fault.director_unavailable', 'scenario.finished', 'job.manual_retry')),
  CONSTRAINT ck_activity_events_severity CHECK (severity IN ('info', 'warning', 'error')),
  CONSTRAINT ck_activity_events_actor_type CHECK (actor_type IN ('system', 'operator', 'worker', 'demo'))
);

CREATE INDEX ix_events_timeline ON activity_events(enquiry_id,event_seq);

CREATE TABLE notifications (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  enquiry_id uuid REFERENCES enquiries(id),
  kind varchar(160) NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  operation_key varchar(256) NOT NULL UNIQUE,
  last_attempt_at timestamptz,
  last_error_code text,
  UNIQUE(enquiry_id,kind),
  CONSTRAINT ck_notifications_status CHECK (status IN ('pending', 'sent', 'disabled', 'delivery_unknown', 'failed')),
  CONSTRAINT ck_notifications_last_error_code CHECK (last_error_code IN ('connect_failed', 'rate_limited', 'unavailable', 'auth_failed', 'validation_failed', 'not_found', 'conflict', 'result_unknown', 'invalid_response', 'director_unavailable'))
);

CREATE TABLE integration_health (
  provider text PRIMARY KEY,
  status text NOT NULL,
  incident_id uuid,
  last_success_at timestamptz,
  last_error_code text,
  blocked_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT ck_integration_health_provider CHECK (provider IN ('hubspot', 'slack', 'ai')),
  CONSTRAINT ck_integration_health_status CHECK (status IN ('ok', 'degraded', 'blocked', 'disabled')),
  CONSTRAINT ck_integration_health_last_error_code CHECK (last_error_code IN ('connect_failed', 'rate_limited', 'unavailable', 'auth_failed', 'validation_failed', 'not_found', 'conflict', 'result_unknown', 'invalid_response', 'director_unavailable'))
);

CREATE TABLE worker_heartbeats (
  worker_id uuid PRIMARY KEY,
  started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  active_job_count integer NOT NULL DEFAULT 0,
  CHECK(active_job_count>=0)
);

CREATE TABLE demo_faults (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scenario_run_id uuid NOT NULL REFERENCES scenario_runs(id),
  provider text NOT NULL,
  mode text NOT NULL,
  status text NOT NULL DEFAULT 'armed',
  remaining_uses integer NOT NULL,
  expires_at timestamptz NOT NULL,
  created_by uuid NOT NULL REFERENCES operators(id),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT ck_demo_faults_provider CHECK (provider IN ('hubspot', 'slack', 'ai')),
  CONSTRAINT ck_demo_faults_mode CHECK (mode IN ('http_503_before_write', 'http_401_before_write', 'http_429_before_write', 'drop_success_response', 'invalid_ai_response', 'crash_after_remote_write')),
  CONSTRAINT ck_demo_faults_status CHECK (status IN ('armed', 'exhausted', 'expired', 'cancelled')),
  CHECK(remaining_uses BETWEEN 0 AND 3)
);

CREATE INDEX ix_faults_run ON demo_faults(scenario_run_id,status);

CREATE TABLE fault_claims (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  match_request_id uuid NOT NULL UNIQUE,
  fault_id uuid REFERENCES demo_faults(id),
  scenario_run_id uuid NOT NULL REFERENCES scenario_runs(id),
  claim_identity varchar(500) NOT NULL,
  result_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(scenario_run_id,claim_identity)
);

CREATE TABLE rate_limit_counters (
  scope varchar(40) NOT NULL,
  subject_hash char(64) NOT NULL,
  window_start timestamptz NOT NULL,
  count integer NOT NULL DEFAULT 0,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY(scope,subject_hash,window_start),
  CHECK(count>=0)
);

CREATE INDEX ix_rate_expiry ON rate_limit_counters(expires_at);
"""


def upgrade() -> None:
    op.execute(_DDL)


def downgrade() -> None:
    op.execute("DROP SCHEMA leadops CASCADE")
