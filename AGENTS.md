# EnquiryTrail

A single-operator lead intake pipeline for a fictional renovation company: an enquiry
arrives, is deduplicated, routed to an owner, prepared in the background, synced to a CRM
contact, and tracked as a local follow-up task.

The repository name is the product name. Internal identifiers — schema, database, package
name and test database prefix — stay `leadops` to match the contract package.

## Quick start

Prerequisites: Docker Desktop running, Python 3.12, and a `.env` file.

    docker compose up -d db          # PostgreSQL 16 on 127.0.0.1:55432
    cp .env.example .env             # DATABASE_URL and the local secrets
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
P1a (this branch) delivers the HTTP CRM adapter and provider simulator. Still missing: persistent worker loop (P1b), P2 business operations, P3 intake security, and the UI (P4).

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
| S7e webhook, rate limits, origin and proxy handling | planned (P3 intake security) |
| S8 extraction and fault injection | planned (P5 AI extraction; fault demonstration P6) |
| P0 reliability closeout (bc1a1c3..652ffd7 + regression pins) | locally complete on fix/p0-closeout; merge/CI gate in docs/closeout-review.md |
| P1a HTTP CRM adapter + provider simulator | implemented on feat/crm-http-adapter (stacked on P0 until P0 merges); PR 2 |
| P1b persistent worker, heartbeat, seed, Compose | worker runtime implemented on feat/p1b-persistent-worker (P1b-1); seed/Compose still planned (P1b-2) |
| P2 business operations/queries (A32, lists, A33, owner actions, integration status, worker health) | planned (P2) |
| S9 UI, delivery | UI planned (P4); connected verification/handover P7; still missing |

Deferred acceptance items: A13, A18, A19, A20, A22, A23, A26, A27, A29, A30, A32, A33.
A07 is proven only for the schema half until the domain phone rejection is green.

## Working protocol

- Red before green. Write one failing behavioural test per slice, confirm it fails because
  behaviour is missing, then implement.
- Separate commits: `test(<slice>): ...`, `feat(<slice>): ...`, `fix(...)`, `chore: ...`.
  A feature commit touches `backend/app/` only. Test edits ride in their own commit and
  need operator approval.
- Never weaken, delete or reorder an assertion to reach green. If a test is contradictory,
  stop and report the exact line.
- Fake and support modules may gain capability additively; existing call sites must behave
  unchanged.
- A test run that is not pasted did not happen. Reports state the real failing set, not the
  intended one.

## Acceptance evidence

Automated evidence lives in the test suite; the operator keeps the signed record in
`docs/acceptance-log.md` (acceptance id, test, commit, date, verdict). Never mark an
acceptance item passed in code or in a comment on the operator's behalf.
