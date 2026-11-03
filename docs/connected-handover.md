# Connected handover and recovery runbook (mock-labelled delivery)

This document is the operator handover for the mock-labelled delivery.
It describes how to start, inspect, and recover the shipped stack, which
runs against the local provider simulator. It is not connected evidence.

## Connected capability classification (mock-labelled delivery)

Connected capability is classified four ways in this delivery. No
live HubSpot account and no live LLM were called. Simulator results
must never be presented as connected evidence, and no acceptance item
is signed off here.

| Classification | Capability |
|---|---|
| Implemented and verified | Mock CRM adapter against the provider simulator (`provider-sim/`); fixture and template extraction (`FixtureExtractor`, `TemplateExtractor`). Verified by the unit and integration suites against the simulator and test doubles. |
| Implemented but unverified | HTTP CRM adapter (`HttpCrmAdapter` in `backend/app/adapters/crm_http.py`) wire behaviour against a real provider. The adapter implements the local wire contract in `docs/crm-http-contract.md`; it has not been run against a real provider in this delivery, so no external provider conformance is claimed. |
| Not implemented | Live extractor for connected mode (`_resolve_extractor` in `backend/app/worker/runner.py` raises `RuntimeError` for connected mode; no live extractor exists in this build); real provider conformance (no real provider was exercised, so conformance to any real provider's behaviour is not implemented or verified). |
| Excluded by scope | Real HubSpot and real LLM calls in this delivery. They are out of scope for this mock-labelled delivery and were never made. |

The live suite in `backend/tests/live/` is opt-in only (`--run-live` or
`RUN_LIVE=1`). Without its prerequisites it fails loudly, naming each
missing item; it never skips. With prerequisites present it would
exercise create, read, optional patch, and a repeat create against the
configured endpoint by calling the real `HttpCrmAdapter` directly.
That is adapter-level wire smoke only; it does not verify intake,
worker sync, or persisted `crm_links`, and it is not A30 evidence: it
only compares a local variable holding the returned remote id across
adapter calls. That happy path has not been run in this delivery
because no real credentials are available.

### A30 follow-up runbook (operator, real provider)

A30 (live provider smoke) stays **PENDING OPERATOR**. The adapter-level
live smoke above does not close it. An operator with a dedicated real
test account closes A30 only by running the full flow below and keeping
the evidence outside this repository:

1. Submit an enquiry through the real intake API (the public intake
   endpoint of a stack configured with `CRM_MODE=http`, a real
   `CRM_BASE_URL` and `CRM_TOKEN`, and a live extractor configuration
   once one exists), using a fresh email on the dedicated test account.
   Record the enquiry id and the submitted email.
2. Run an independent worker process configured with the real provider
   adapter (`HttpCrmAdapter` against the real provider, not the
   simulator) and let it claim and sync the enquiry. Record the worker
   logs (`job.handled`, `crm.synced`) for that enquiry.
3. Verify the persisted link: read the local `crm_links.remote_id` for
   the enquiry's contact and compare it with the real remote contact id
   returned by a read-by-email against the real provider. The two must
   be equal, and exactly one local link must exist for the contact.
4. Submit a repeat enquiry for the same contact (same email) through
   the real intake API and let the worker sync it. Verify identity
   reuse: the repeat sync resolves to the same `crm_links.remote_id`,
   no second local link is created, and a read-by-email against the
   real provider shows exactly one remote contact for that email (no
   duplicate remote contact).
5. Run the M0-gated checks below on the dedicated account: the
   lost-response exercise (visibility after a lost write) and the
   patch-preservation exercise (patch with empty-field preservation),
   each with the evidence listed in the M0 table. A30 is not closed
   while any M0 row it depends on lacks live evidence.

Record each step's timestamps, remote ids, and provider-side records
with the operator's evidence; do not commit real endpoints, tokens, or
remote data to this repository.

### Mock-only exception

This mock-labelled delivery carries a mock-only exception for connected
acceptance: connected (live-provider) acceptance for this delivery may
be evidenced against the provider simulator and the mock CRM path
instead of a real provider, because no real account is in scope.

The exception exempts only that: connected acceptance evidence for
this mock delivery. It does **not** exempt C01 or C02, and it does not
exempt any other delivery gate (including the CI merge gate, the
M0 checks above for any future connected claim, or operator sign-off
of acceptance verdicts, which stay PENDING OPERATOR).

### Open M0 checks

The following M0 checks remain open. Each lists the evidence that will
close it; none is closed by simulator or fixture results.

| M0 check | What must be proven | Evidence that closes it |
|---|---|---|
| HubSpot token scope | The dedicated test-account token can create, read, and (if used) patch contacts, and is rejected or scoped as expected outside that account. | A live run of `backend/tests/live/` with `--run-live` against the dedicated account, plus the provider-side scope record for that token, kept by the operator outside this repository. |
| Visibility after a lost write | After a create whose response is lost, the contact becomes visible to a read-by-email within the readback budget, so reconciliation finds it instead of creating a duplicate. | A live lost-response exercise on the dedicated account showing one remote contact, one crm link, and a reconciled operation; timestamps of write, visibility, and readback kept by the operator. |
| Patch with empty-field preservation | A patch fills only confirmed-empty remote fields and preserves every non-empty remote field the local record does not own. | A live patch exercise on the dedicated account with before/after remote field values, showing preserved fields unchanged and no raw values in events (event payloads stay masked). |
| LLM output interface | The connected extractor's real output shape, model identifier, and failure behaviour match the `ExtractorPort` contract the fixture and template adapters implement. | A live extraction exercise with the real model: request/response shape, validated budget/timeframe/evidence handling, parameter snapshot, and proof that a failure leaves the original enquiry and task untouched and never falls back to fixture data. |

Until those rows have live evidence, treat every implemented-but-
unverified or not-implemented "connected" statement about this system
per the classification above, including this document's live-suite
description (it describes code structure, not a run).

## Starting the stack from a clean volume

Prerequisites: Docker Desktop running, Python 3.12 for host-side test
runs, and a `.env` file created from `.env.example`. Set
`SESSION_SECRET` to a private random value and
`SEED_OPERATOR_PASSWORD` to a demo operator password in `.env`. There
is no default operator password, and no secret belongs in this
repository.

From a clean volume (no previous database data):

```sh
cp .env.example .env   # then set SESSION_SECRET and SEED_OPERATOR_PASSWORD
docker compose down -v          # only when discarding local demo data is intended
docker compose up -d --build    # db, provider-sim, migrate, api, worker, web
```

Service order is enforced by Compose health and completion conditions,
not by the API migrating on start (the API never migrates):

1. `db` (PostgreSQL 16) becomes healthy (`pg_isready`).
2. `provider-sim` becomes healthy on its internal port 8091. It is
   never published to the host.
3. `migrate` runs only after `db` is healthy. Its command is exactly
   `alembic upgrade head && python -m app.seed`: migrations first, then
   the seed. It runs once (`restart: "no"`) and must complete
   successfully before dependents start.
4. `api` and `worker` start only after `migrate` completes and
   `provider-sim` is healthy.
5. `web` starts after `api` is healthy and proxies `/api` to it.

The API is at `http://127.0.0.1:8080` (liveness:
`GET /api/v1/health/live`, readiness: `GET /api/v1/health/ready`) and
the UI at `http://127.0.0.1:5173`. Both are loopback-only.

The seed is idempotent: owners come from the routing configuration,
known provider health rows (`hubspot`, `slack`, `ai`) are inserted once
and never reset, and the operator is created from
`SEED_OPERATOR_USERNAME` / `SEED_OPERATOR_PASSWORD` only when absent.
An existing operator password is never reset by re-running the seed.

For host-side tests only, `docker compose up -d db` is enough; the
integration suite creates its own throwaway `leadops_test_<hex>`
database, migrates it, and drops it afterwards. It never touches the
development database and never falls back to SQLite.

## Reading integration status and resuming a blocked provider

Integration health is an operator read, not a log guess:

- `GET /api/v1/integrations` (authenticated session) returns one row
  per provider: `provider`, `status`, `incident_id`,
  `last_success_at`, `last_error_code`, `blocked_at`, `updated_at`.
  Seeded providers are `hubspot`, `slack`, and `ai`.
- A provider becomes `blocked` only on an `auth_failed` provider error
  (HTTP 401/403 through the CRM adapter). Blocking opens one incident
  and emits one `integration.blocked` event; concurrent failures keep
  the same incident instead of opening new ones.
- While a provider is blocked, its sync jobs wait instead of calling
  the provider. Fix the credential or provider problem first; resuming
  without fixing it only re-blocks.

Resume is a versioned operator action (session plus CSRF token):

- `POST /api/v1/integrations/{provider}/resume` with a body of
  `expected_status: "blocked"`, the `expected_updated_at` exactly as
  read from the status list, and a `reason` of 10 to 500 characters.
- The service re-reads the row under a lock: a missing provider is a
  404, a provider that is not blocked or whose `updated_at` no longer
  matches is a 409, and nothing is written on either conflict.
- It then probes the provider inside that same transaction. A failed
  probe is a 409 `resume_failed` and changes nothing: the provider
  stays blocked with the same incident.
- A successful probe marks the provider `ok`, requeues its blocked
  sync jobs in a new cycle due immediately, and emits exactly one
  `integration.resumed` event carrying the operator and reason.

## Manual job retry

Individual dead or blocked jobs are retried from the job action, not
by editing rows:

- `POST /api/v1/jobs/{id}/retry` (operator session plus CSRF token)
  with `expected_state` (`"dead"` or `"blocked"`), the job's
  `expected_updated_at` as last read, and a `reason` of 10 to 500
  characters.
- The conditional transition and the single `job.manual_retry` event
  commit together. A state mismatch is a 409 `invalid_state`, a stale
  `expected_updated_at` is a 409 `version_conflict`, and a conflicted
  call writes nothing: no transition and no event.
- A retry never touches the recorded provider operation. An operation
  whose result is still unknown therefore routes the next claim to
  readback (below), and a failed or absent operation re-enters the
  normal sync path. Retrying a job cannot cause a duplicate create.

Automatic retry precedes manual retry: retryable failures wait 1, 4,
then 16 seconds (at most 0.5 s jitter), and a provider `Retry-After`
is honoured up to a 300 s cap. Jobs lease for 60 s; leases, heartbeats,
session expiry, and rate limits use the database clock, while business
fields use the business clock. Do not swap them when diagnosing times.

## Recovering from a lost CRM create response

A lost create response means the provider may have written the contact
while the caller saw a timeout, reset, or 5xx. The system treats that
as `result_unknown`, never as "safe to create again":

1. The create intent (operation key and request hash) is recorded
   before the create is dispatched, so the unknown operation survives
   the failure.
2. The next claim takes the readback-only path: it reads the contact
   by email instead of sending a second create. The first readback
   follows the unknown result after 1 s, the next after 4 s.
3. If the readback finds the contact, the operation is marked
   reconciled, the crm link stores that contact's remote id, and
   `crm.recovered_existing` plus the `crm.synced` event are recorded. Exactly one
   remote contact and one link result.
4. If the readback finds nothing, the operation stays unresolved and
   readback repeats on the retry schedule. A failed readback never
   marks anything synced.
5. A definite create conflict (HTTP 409) is resolved the same way, by
   the single read-by-email inside the create call's remaining
   deadline budget. It is never a second create.

Operator view: the enquiry detail shows a reconciling state while
readback is pending and moves to ready/synced only after the link is
recorded. If a job exhausts its budget and goes dead, use the manual
job retry above; the retried claim still goes to readback first
because the operation record was never changed.

## Worker heartbeat and stale signals

Worker health is the heartbeat rows, read from
`GET /api/v1/health/workers` (authenticated session):

- Each worker upserts a heartbeat row (`worker_id`, `started_at`,
  `last_seen_at`, `active_job_count`) at the configured heartbeat
  interval (default 5 s, `WORKER_HEARTBEAT_SECONDS`). Job leases are
  renewed by a separate per-job heartbeat while a claim is handled.
- A worker is `alive` while its last heartbeat is within the stale
  threshold and `stale` afterwards. The threshold is three missed
  heartbeats, at least 15 s (`stale_after_seconds` in the response).
- `stale` means "no heartbeat was recorded in time", not "the worker
  crashed at a known moment". Check the worker process and its logs
  (`worker.started`, `worker.stopped` with `clean`, `worker.slot_error`,
  `job.heartbeat_fence_lost`) before restarting anything.
- A worker that stops uncleanly exits non-zero so a supervisor or
  Compose restart policy can act. On a clean stop it writes a final
  heartbeat with zero active jobs.
- There is deliberately no HTTP healthcheck for the worker container:
  its heartbeat row is its health signal, so do not invent one.

## Fault-demo scenario controls

The controlled failure demonstration (Demo page in the UI, headed Failure demonstration; scenarios
API behind it) injects faults only into the simulator path. It never
touches a real provider, and its results are demonstration evidence,
not connected evidence.

- Create a run: `POST /api/v1/scenarios` with a `scenario_id`. List
  and inspect runs with `GET /api/v1/scenarios` and
  `GET /api/v1/scenarios/{run_id}`.
- Arm a fault: `POST /api/v1/scenarios/{run_id}/faults` (operator
  session plus CSRF token) with a `provider` (`hubspot`, `slack`, or
  `ai`), a `mode`, `uses` (1 to 3), and `expires_in_seconds` (1 to
  86400). Modes are `http_503_before_write`, `http_401_before_write`,
  `http_429_before_write`, `drop_success_response`,
  `invalid_ai_response`, and `crash_after_remote_write`.
- Faults are consumed atomically per use and expire on schedule; an
  expired or exhausted fault no longer matches. Finishing a run with
  `POST /api/v1/scenarios/{run_id}/finish` evaluates its assertions
  and records the outcome on the run.
- If the fault matcher itself is unavailable, the run is inconclusive
  and business work is never blocked by the demonstration machinery.
  Treat an inconclusive run as no evidence, not as a pass.
- The simulator's own test surface (`/__test__/fault`, `/__test__/reset`,
  `/__test__/counts` on the internal simulator port) is local-only.
  Never expose the simulator or that surface publicly.

## Connected-profile startup pinning

Bad connected configuration fails at startup, by design:

- `CRM_MODE=http` without both `CRM_BASE_URL` and `CRM_TOKEN` raises
  `ValueError("CRM_BASE_URL and CRM_TOKEN are required in http mode")`
  when settings are constructed, before any service starts. There is
  no fallback to a fake CRM. A malformed `CRM_BASE_URL` (non-HTTP(S)
  or hostless) is rejected the same way.
- `EXTRACTOR_MODE=connected` without an explicit `EXTRACTOR_MODEL`
  raises `ValueError("EXTRACTOR_MODEL is required in connected mode")`.
  Via the environment, an unset model no longer inherits the fixture
  default; `backend/app/settings.py` passes an empty model in
  connected mode so the same check fires.
- With a model configured, connected extraction still resolves to a
  loud failure at wiring time: `_resolve_extractor` raises
  `RuntimeError("connected extraction is gated on the live-extraction
  finding; no live extractor is available in this build")`, and
  building the application with those settings raises the same error.
  Connected extraction never substitutes fixture or template data.
- These behaviours are pinned by
  `backend/tests/unit/test_connected_pinning.py` and the CRM cases in
  `backend/tests/unit/test_crm_settings.py`.

## Placeholders and secrets

Examples in this repository use placeholder endpoints such as
`https://crm.example.invalid` and placeholder tokens. Real endpoints,
real tokens, and the dedicated test-account credentials are supplied
by the operator through the environment (`.env`, untracked) at run
time. Never commit them, paste them into issues or logs, or copy them
into this document. Preserved-field event payloads stay masked (first
character plus `***`); a raw value in an event is a defect to report,
not data to share.
