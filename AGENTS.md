# EnquiryTrail

A single-operator lead intake pipeline for a fictional renovation company: an enquiry
arrives, is deduplicated, routed to an owner, prepared in the background, synced to a CRM
contact, and tracked as a local follow-up task.

The repository name is the product name. Internal identifiers — schema, database, package
name and test database prefix — stay `leadops` to match the contract package.

## Quick start

Prerequisites: Docker Desktop running, Python 3.12, and a `.env` file.
Set `SESSION_SECRET` to a private random value and `SEED_OPERATOR_PASSWORD`
to a demo operator password of your choosing in `.env`; there is no default
operator password. The `migrate` service runs `alembic upgrade head` and the
seed before `api` and `worker` start.

    cp .env.example .env             # then set SESSION_SECRET and SEED_OPERATOR_PASSWORD in .env
    docker compose up -d --build     # db, provider-sim, migrate (migrate + seed), api, worker
    # open the API at http://127.0.0.1:8080 (health: /api/v1/health/live)

For tests only, `docker compose up -d db` (PostgreSQL 16 on 127.0.0.1:55432) is enough:

    cd backend
    .venv\Scripts\python.exe -m pytest tests/unit -q
    .venv\Scripts\python.exe -m pytest tests/integration -q

`uv run pytest ...` is equivalent when `uv` is on PATH. The integration suite needs the
compose database: it creates `leadops_test_<hex>`, applies the Alembic migration, truncates
between tests and drops the database afterwards. It never touches the development database
and never falls back to SQLite.

Migrations are explicit: `cd backend` then `alembic upgrade head` with `DATABASE_URL` set.
The API never migrates on start-up.

## Layout

    backend/app/domain/       pure functions: normalization, routing, canonical hashing
    backend/app/ports/        Protocol boundaries: BusinessClock, CrmPort, Scheduler
    backend/app/services/     use cases: intake, crm_sync, health, review, tasks, scheduler
    backend/app/db/           SQLAlchemy Core tables and repository functions
    backend/app/worker/       claim, lease, fence, dispatch
    backend/app/api/          HTTP validation, auth, error envelope
    backend/app/security/     passwords, sessions, CSRF
    backend/app/config/       app-owned router configuration, hash-guarded
    backend/app/adapters/     HTTP CRM adapter (crm_http.py); provider-sim/ is the standalone simulator
    backend/app/bootstrap.py  the single composition root

Dependency direction: `api -> services -> (domain | ports | db)`. Adapters implement ports
and the worker reuses services. Keep SQL in `app/db` and decisions in `app/services`.

## What the pipeline does

Intake persists one receipt, contact, enquiry, prepare job, follow-up task and an
`enquiry.received` event in one transaction. The worker claims the prepare job, stamps
`local_prepared_at`, emits `enquiry.prepared` and enqueues one `sync_contact` job per
contact. The CRM sync creates or preserves the remote contact and records `crm_links`.
Retryable failures wait 1/4/16 seconds; a lost create response is reconciled by readback;
`auth_failed` blocks the provider until an operator probe succeeds.

## Contracts and hard boundaries

- The plan package lives outside this repository. It is read-only: never copied, committed
  or read at runtime.
- `backend/app/config/routing.py` is an app-owned transcription of the routing contract,
  guarded at start-up by its authority hash. A mismatch must fail boot. Do not edit it
  without re-deriving the hash.
- `backend/tests/support/routing_config.py` is the test-side transcription of the same
  object, guarded by the same hash. The two copies must agree; the hash is the guard.
- PostgreSQL only, schema `leadops`, every statement schema-qualified.
- No secrets in the repository. `.env` is generated from `.env.example` and is untracked.
- Dependency versions are pinned by `uv.lock` and `backend/pyproject.toml`.

## Decisions that must not drift

- CSRF tokens are `HMAC-SHA256(SESSION_SECRET, raw session token)` and are stored only as
  hashes. Rotating `SESSION_SECRET` invalidates every live session's CSRF token by design.
- Preserved CRM fields are masked as the first character plus `***` in
  `crm.field_preserved`; never put a raw value in an event.
- Business fields use the BusinessClock; leases, heartbeats, session expiry and rate limits
  use the database clock. Never swap them.
- Lease 60 s, retry 1/4/16 s with at most 0.5 s jitter, `Retry-After` capped at 300 s, and a
  600 s content-dedup window anchored on the most recent canonical receipt.
- Tests assert persisted facts and public outputs. Private call counts and SQL text are
  forbidden.
- Known divergence: owner, routing and the follow-up task are created in the intake
  transaction, not by the prepare job. The `processing` detail state with null owner and
  null task is therefore not reachable in this build; do not "fix" intake to match it.

## State

Phase map (authoritative): P0 reliability closeout; P1 runnable mock
backend (P1a HTTP CRM adapter + independent simulator, P1b persistent
worker/heartbeat/seed/Compose); P2 business operations and queries
(contact-version coordination A32, enquiry/task lists, event timeline
A33, owner actions, integration status/resume, worker health); P3 intake
security (exact Origin, body-size bound, rate limits, trusted proxy,
signed webhook); P4 core UI and template drafts; P5 AI extraction and
fallback; P6 controlled failure demonstration; P7 connected verification
and handover. UI is P4 and intake security is P3, never P2.
All of P1a, P1b, P2, P3, P4, P5 and P6 are implemented on local `main` (see the status table below). P7 connected capability is classified four ways in `docs/connected-handover.md`: implemented and verified (mock CRM adapter vs provider-sim, fixture/template extraction), implemented but unverified (HTTP CRM adapter wire behaviour against a real provider), not implemented (live extractor for connected mode; real provider conformance), and excluded by scope (real HubSpot/LLM calls in this delivery); M0 remains open.

| Area | Status |
|---|---|
| S1 normalization and routing | complete |
| S2 intake persistence | complete |
| S3 idempotency, deduplication, contact identity | complete |
| S4 worker claim, lease, fence, local prepare | complete |
| S5 CRM sync and field preservation | complete |
| S6 retry, reconciliation, provider blocking | complete |
| S7a API sessions and CSRF | complete |
| S7b intake endpoint | complete |
| S7c read endpoints and the detail shape | complete |
| S7d operator actions and conflict mapping | complete |
| S7e webhook, rate limits, origin and proxy handling | complete: exact Origin, body bound, trusted proxy (P3a), rate limits and signed webhook (P3b/P3c) implemented and covered by integration tests |
| S8 extraction and fault injection | complete: AI extraction with fixture/template adapters and fallback (P5) and the controlled fault demonstration (P6) implemented; extraction fixture and 20-case matrix covered by tests |
| P0 reliability closeout (bc1a1c3..652ffd7 + regression pins) | complete on local `main`; merge/CI gate wording in docs/closeout-review.md is historical (2026-10-26) - current CI gate is H03 in docs/checklist.md |
| P1a HTTP CRM adapter + provider simulator | complete on local `main` (HTTP CRM adapter, ChargeManager-free, plus independent provider simulator) |
| P1b persistent worker, heartbeat, seed, Compose | complete on local `main`: persistent worker loop, heartbeat rows and `GET /api/v1/health/workers`, seed, Compose stack |
| P2 business operations/queries (A32, lists, A33, owner actions, integration status, worker health) | complete on local `main`: A32 contact-version coordination, enquiry/task lists, A33 event timeline, owner actions, integration status/resume and worker health all implemented (acceptance remains operator-signed); changed contact data is a new enquiry under the existing canonical fingerprint (strict dedup, no content-only dedup) |
| S9 UI, delivery | UI complete (P4 core UI implemented); Slack notifications and fault demo implemented; connected capability classified four ways (implemented+verified: mock CRM vs provider-sim, fixture/template extraction; implemented+unverified: HTTP CRM adapter vs a real provider; not implemented: live extractor, real provider conformance; excluded by scope: real HubSpot/LLM calls) pending M0 (P7, see docs/connected-handover.md); `scripts/perf_measure.py` and `docs/perf-evidence.md` record a local sample only |

Acceptance verdicts all stay PENDING OPERATOR in `docs/acceptance-log.md`. A26 has automated process-level evidence plus a compose-form live record (2026-11-02 PASS, `docs/a26-compose-recovery-evidence.md`); items without live-provider automated evidence: A30 only. C01/C02 are OFFLINE contract checks blocked on the missing full authority package (missing: authority.md, openapi.yaml, vocabulary.yaml, generate_vocabulary.py, contract fixtures); they are not live-provider evidence. A22 evidence is complete, including the dedicated e2e injection case in `e2e/core-ui.spec.ts`; its verdict stays PENDING OPERATOR. Connected mode (M0 O01-O04) is UNVERIFIED.

## Working protocol

Test-first is required. A separate red commit is not.

- A behaviour defect starts with a regression test that reproduces it. Record what failed
  before the fix and what the fix verifies. The failing state may be committed or not; it
  is evidence, not a delivery unit.
- One commit is one complete logical change: implementation, its tests, and any doc change
  that belongs to it, together. Name it for what changed: `fix(worker): ...` for a defect,
  `feat(jobs): ...` for new capability. The type describes the change, not the phase of the
  cycle in which it was written.
- A commit may touch tests, app code and docs at once when they form one change. Changing an
  existing expectation needs a stated reason - contract change, wrong assertion, or
  implementation coupling - in the commit body or the report.
- Development branches may be red while work is in progress. The merge gate is green CI on
  the exact commit that lands; a local run or an earlier commit does not substitute for it.
  Small PRs squash by default; a PR carrying several independent fixes may keep one commit
  per fix, each verifiable on its own.
- Never weaken, delete or reorder an assertion to reach green. If a test is contradictory,
  stop and report the exact line.
- Fake and support modules may gain capability additively; existing call sites must behave
  unchanged.
- A test run that is not pasted did not happen. Reports state the real failing set, not the
  intended one.

See `docs/development-process.md` for the full policy, its rationale and the merge gate.

## Acceptance evidence

Automated evidence lives in the test suite; the operator keeps the signed record in
`docs/acceptance-log.md` (acceptance id, test, commit, date, verdict). Never mark an
acceptance item passed in code or in a comment on the operator's behalf.
