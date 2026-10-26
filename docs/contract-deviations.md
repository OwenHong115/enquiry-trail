# Contract deviations and delivery boundaries

Authority remains the external v3.0 contract package. This file records decisions and
references; it does not redefine its schemas or vocabularies. No acceptance item is
signed off here.

## Intentional local divergence

- Intake creates routing, owner and the initial follow-up task atomically. The
  unprepared detail therefore already contains owner/task. See AGENTS.md and
  `test_api_detail.py::test_processing_detail_right_after_submit`.
- The cycle budget counts every claim, including readbacks and recovered crashes.
  Budget exhaustion is decided only by claim_next. A fourth failure records a
  continuation; the next claim closes it without waiting for its retry timestamp,
  without a fifth attempt and without a provider call. This supersedes the old R1
  expectation that the failure handler itself closes the fourth attempt.
  See test_r1_retry_budget.py and test_r9_budget_ceiling.py. Keep the schema ceiling.
- On budget exhaustion an unresolved intent becomes unknown. Manual retry preserves
  that operation and only reads back. A definite pre-write 401 instead closes the
  operation as failed, allowing create after provider resume (R15/R16).
- New password hashes and the missing-user dummy use PBKDF2-SHA256 with 600,000
  iterations. Existing hashes retain their stored work factor and remain verifiable;
  automatic rehash-on-login is not implemented in this slice.

## A32 contact-version coordination (decision, 2026-10-26)

- Field-merge policy for the app-owned fields firstname/lastname/phone (email is
  identity, never patched): at the baseline version a pre-existing remote contact
  is adopted as-is — blank remote fields are filled, differing non-blank remote
  values are preserved and recorded as masked `crm.field_preserved` events. When
  the local contact version has advanced beyond the version already synced,
  changed app-owned fields are sent with `ContactFieldPatch` and overwrite the
  remote values; patched fields record no preservation event.
- A required overwrite that cannot be applied (provider does not support patch,
  or the patch call fails) never advances `crm_links.synced_contact_version`:
  the failure is recorded through the job failure path (retry, blocked or dead
  by error code), so the enquiry does not report ready/synced for that version.
  `crm.synced` and the link record only the version actually applied.
- Unknown operations stay readback-first: the readback applies the same
  advanced-version patch before the operation is recorded reconciled, and a
  patch failure leaves the operation unresolved.
- Content deduplication stays strict canonical-content dedup: the content
  fingerprint is unchanged over the full canonical payload, and only an
  identical full payload within the 600s window takes the duplicate path.
  A resubmission with changed first_name/last_name/phone is a new enquiry
  under that fingerprint; it updates the contact to the new version, and
  its prepare step rearms the stable sync job for the CRM patch. There is
  no content-only dedup.
- Sync finalization serializes against intake on the same per-email advisory
  lock intake holds for the contact update, so a version committed mid-sync is
  either seen by the finalization (which rearms the stable job) or rearmed by
  the later prepare step. No duplicate CRM create results in either ordering.
- This records the local decision only; A32 acceptance remains the operator's
  record in docs/acceptance-log.md and is not signed off here.

## P2 enquiry/task lists (local list-shape assumptions)

No complete external list schema exists in this repository, so the list
shapes are strict subsets of the existing detail/task public fields:

- `GET /api/v1/enquiries` items: id, display_no, received_at (the enquiry's
  creation instant; the detail exposes no separate created_at), service_type,
  processing_summary, attention, crm_status (the detail's crm.status,
  flattened), next_attempt_at exactly when the detail would publish it,
  owner, and the detail contact summary. Stable order is
  received_at DESC, id DESC.
- `GET /api/v1/tasks` items: task id/kind/title/status/due_at/created_at/
  completed_at/cancelled_at/version, owner, and a minimal enquiry summary
  (id, display_no, service_type, contact name/email/phone). Order is
  created_at DESC, id DESC (the task keyset).
- Both return `{"items": [...], "next_cursor": string|null}` with opaque
  keyset cursors over (timestamp, id); invalid cursors are 422, never a
  silent restart. limit defaults to 25, max 100.
- Status values come from the one shared read-model projection
  (app/services/read_model.py) used by the detail endpoint; there is no
  second mapping. Owner is a plain code filter (an unknown code matches
  nothing); the status/attention vocabularies are validated and unknown
  values are 422. Tasks add an optional `overdue=true` filter (open and
  due_at before the business clock).

## A33 enquiry event timeline (local decisions)

- `GET /api/v1/enquiries/{enquiry_id}/events` returns visible events in
  event_seq ASC order as `{"items": [...], "next_cursor": string|null}`.
  Items expose id, event_type, severity, actor_type, actor_id, details and
  created_at; event_seq itself is never exposed - the opaque cursor
  (base64 of the last returned event_seq) carries it. Pages read
  event_seq > cursor with limit+1; invalid cursors are 422.
- Visibility: the audit-only set is exactly {job.manual_retry}; those
  events may consume event_seq and are simply skipped by cursors.
  integration.resumed has enquiry_id=None and can never appear on an
  enquiry timeline. Every other event type is visible.
- Commit safety: every visible event writer takes a transaction-scoped
  advisory lock keyed by enquiry_id (app/db/event_repo.py, namespace
  `enquiry_event`) before inserting, and holds it to commit, so
  per-enquiry seq allocation and commit are serialised and a polling
  reader can neither skip nor duplicate an event. Audit-only writers
  do not take the lock.
- Lock order is consistent everywhere: existing row locks first (job,
  contact, enquiry, task and health rows), then the event advisory
  lock, taken inside the event insert functions themselves.

## P2 integration status, resume, worker health (local decisions)

- Seed: `seed()` idempotently inserts integration_health rows for the known
  providers (hubspot=ok, slack=disabled, ai=disabled) with
  ON CONFLICT (provider) DO NOTHING, so an existing blocked/ok row is never
  reset and `seed()`'s return shape is unchanged. Existing databases need
  `python -m app.seed` once (Compose's migrate service already runs it).
- `GET /api/v1/integrations` returns the existing rows sorted by provider as
  `{"items": [...]}` with no pagination; an unseeded database returns items
  only for rows that exist. The API resolves the CRM port once in the
  composition root via the worker's resolver (`app.state.crm`);
  tests may substitute a fake there.
- `POST /api/v1/integrations/{provider}/resume` (session + CSRF): unknown
  provider -> 404; provider row absent -> 404; expected_status must be
  "blocked" (body errors are 422). Under the row lock: not blocked -> 409
  `invalid_state`, stale expected_updated_at -> 409 `version_conflict`,
  failed probe -> 409 `resume_failed` (chosen code; a failed probe changes
  nothing - no status change, no requeue, no event). On success one
  transaction sets ok (blocked_at/last_error_code cleared, last_success_at
  set), requeues blocked sync jobs in a new cycle due now, emits exactly
  one integration.resumed, and returns the integration item.
- F10 limitation: the provider probe runs inside the row-locked resume
  transaction, bounded by the 15s adapter deadline; the row lock is held
  for the probe's duration in this slice.
- `GET /api/v1/health/workers` returns `{"workers": [...],
  "stale_after_seconds": N}` with no queue metrics. A worker is stale when
  its last_seen_at is older than max(3 x WORKER_HEARTBEAT_SECONDS, 15s),
  compared against the database clock, not a Python wall clock.
- Field semantics: `last_success_at` means the last successful probe/resume
  for the provider (set when a resume succeeds, including the worker's
  reconcile path); individual CRM sync calls do not move it. `incident_id`
  is retained when the provider returns to ok: it identifies the most
  recent incident for traceability, and a later block opens a new one.
  `expected_status` is checked explicitly against the locked row (the
  only accepted value is "blocked", so a mismatch is invalid_state).
- Clean shutdown: a worker that stops cleanly leaves its heartbeat row
  behind; it therefore reads alive until the stale threshold passes and
  stale afterwards. No heartbeat row is deleted on shutdown in this slice.
- The Compose api service carries the same CRM_MODE/CRM_BASE_URL/
  CRM_TOKEN configuration as the worker and depends on provider-sim
  healthy, so the API's startup-resolved adapter probes
  provider-sim:8091 rather than a container-local 127.0.0.1. The
  provider-sim port remains unpublished.

## Delivery boundaries (not implemented features)

- Adapter and simulator exist (P1a); the persistent worker loop,
  heartbeat, seed and Compose exist (P1b); no UI (P4); no Origin/body/
  rate-limit/webhook hardening (P3); no connected/live CRM; no
  acceptance signoff.
- Public deployment hardening (HTTPS cookie policy, origin policy, rate limiting,
  request-size controls) is P3/P7 work, not authorized by this slice.
- Readiness uses bounded pool checkout (1s), connection establishment (2s) and SQL
  execution (1s). These bound stages, not every possible network/OS failure or a
  strict end-to-end HTTP deadline. Tests cover unavailable DB and exhausted pool.
- Configure application logging after host logging setup. Application JSON has one
  owned console sink; host console handlers exclude application records, while
  host non-application records and in-memory/file capture remain available.

## Follow-up for the planned contract revision

Reconcile the cycle-closure observation point and intake/preparation timing with v3.1.
Read the published vocabulary and behavior precedence when adding states. The HTTP
retry now uses existing related_jobs fields; no new public contract is needed.

## Decisions confirmed 2026-10-26 (P0 closeout)

- Readback NotFound ladder is anchored at the unknown instant: next_attempt_at exactly T+1, T+5, T+21 (1/4/16 via claim cycle_attempt_count), pinned by test_closeout_regressions.py with absolute instants.
- Create CallContext.external_operation_id equals the inserted external_operations row id; the readback context carries the same id. Pinned by the same file; FakeCrm records contexts.
- P0 lands as its own PR (fix/p0-closeout from 652ffd7) before any CRM adapter work; adapter and closeout are never mixed in one PR. Order is the authoritative phase map: P0 closeout; P1 runnable mock backend (P1a adapter+simulator, P1b worker/Compose); P2 business operations/queries; P3 intake security; P4 UI; P5 AI extraction; P6 failure demonstration; P7 connected verification/handover.

## Decisions confirmed 2026-10-26 (P1a adapter)

- Create 409 is definite already-exists: the adapter reads back by email and returns the found contact (one extra read, never a second create); a missing readback keeps conflict.
- Network-across-transaction limitation (recorded, not silently left): begin_sync lookup and health.resume_provider probe still perform network calls inside DB transactions, because restructuring to snapshot-network-revalidate would rewrite the fenced claim/blocked/unresolved flow and the intent-commit-before-create guarantee. The adapter enforces a hard monotonic total deadline (thread-abort at deadline + 0.3s grace, pinned by test_crm_deadline.py) inside those calls, bounding the transaction hold. Revisit in P1b with the persistent worker.
- No complete external wire contract exists in this repo; docs/crm-http-contract.md is a minimal local contract between the adapter and provider-sim, not provider conformance. No real CRM is called.

- Known follow-up (P1b-1): an unhandled non-provider exception from handle_claim is logged as job.error by run_once, but the job stays running until lease expiry rather than being immediately closed/rescheduled, because its finalization state is uncertain after partial commits. Lease recovery then reclaims it. Immediate rescheduling needs a fenced failure-finalization design.
