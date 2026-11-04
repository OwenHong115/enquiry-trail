# Development plan — post-P0 snapshot

## Portfolio contract completion (2026-11-04)

This section defines D07, C01 and C02 for the mock-only portfolio
delivery. The external v3.0 contract package is unavailable. For this
delivery the application owns the public HTTP contract, exported from
FastAPI's `app.openapi()` using local mock settings. This supersedes
the external-input requirement for these three portfolio checks. The
historical v3.0.1 plan and its original verification record retain
their scope. Record portfolio results separately from original
contract conformance and operator acceptance.

| Item | Requirement | Completion evidence |
| --- | --- | --- |
| D07 | Export the OpenAPI schema offline, with no database connection or provider calls, and generate the TypeScript API types used by the portfolio UI. Reuse the existing Pydantic request and response models. Keep form state and view-only types separate. | One documented generation command with pinned dependencies, a clean diff after regeneration, and a passing frontend build and existing tests. The UI imports the generated API types through its existing API layer. |
| C01 | Check the published enum sets for enquiry `processing_summary`, `attention` and `crm.status` against the table below. Use the existing API declarations as the implementation source. Check the list status fields against the same sets and derive the frontend types from that export. | One focused check of the exported schema, including its referenced definitions. The check detects a missing, extra or renamed value. D07's regeneration check covers frontend drift. |
| C02 | Validate one synthetic enquiry detail response for each of the six summary states against the exported schema for `GET /api/v1/enquiries/{enquiry_id}`. Reuse JSON responses from existing integration cases where available and add only missing cases. | Six validated responses with the expected state combinations below. Validate the public JSON, including required fields and nullability, using the schema with its local references. |

| Field | Expected values |
| --- | --- |
| `processing_summary` | `processing`, `ready`, `waiting_retry`, `blocked`, `reconciling`, `failed` |
| `attention` | `none`, `review_required`, `retry_scheduled`, `notification_uncertain`, `integration_blocked`, `needs_intervention` |
| `crm.status` / `crm_status` | `pending`, `syncing`, `synced`, `waiting_retry`, `reconciling`, `blocked`, `failed` |

Use the following representative cases with no pending human review.
These are test cases for the existing behavior. Other valid combinations
remain governed by the existing read model.

| Case | `processing_summary` | `attention` | `crm.status` |
| --- | --- | --- | --- |
| Immediately after intake, before worker claim | `processing` | `none` | `pending` |
| Prepared and current contact version synced | `ready` | `none` | `synced` |
| Retry scheduled | `waiting_retry` | `retry_scheduled` | `waiting_retry` |
| Sync blocked by the provider | `blocked` | `integration_blocked` | `blocked` |
| Unknown write awaiting readback | `reconciling` | `none` | `reconciling` |
| Sync job terminal after failure | `failed` | `needs_intervention` | `failed` |

For the processing case, the assigned owner and follow-up task are
already present, as in the current intake transaction. For the retry
case, check that `crm.next_attempt_at` is populated. For the ready
case, check that the linked CRM ID is present. Reuse the existing
integration fixtures, runners and CI jobs. Keep the work in one small
PR. The existing routing authority hash, database schema, queue and
recovery behavior, and mock-only delivery scope remain binding.

## Snapshot (2026-10-26)

- Snapshot below is historical (2026-10-26). Measured at base
  `a305db162285f5c65e1468ca563480b900587dac` on 2026-11-01: local
  `main` was 0 behind / 26 ahead of `origin/main`
  (`5872be3a1a7a0ab6754ea4fea472d53beea3d39a`). This docs branch and
  subsequent local merges stay unpushed until the final push.
  Suites then: backend unit 156 passed, backend integration 270
  passed, frontend 14 passed - see `docs/acceptance-log.md`.
- P0 closeout: local `fix/reliability` at `652ffd7` (10 commits
  `bc1a1c3..652ffd7`, ahead of `origin/fix/reliability`), carried onto the
  reviewable branch `fix/p0-closeout` plus two regression pins in
  `test(closeout)` commit `3e45814` (tested revision recorded in
  `docs/closeout-review.md`).
- Verified locally on the closeout revision: 45 unit + 130 integration
  green against real PostgreSQL (128 pre-existing + 2 new pins); 0
  leftover `leadops_test_%` databases and 0 test connections afterwards.
  This is local evidence, not CI and not a merge.

## Order

Phase map (authoritative): P0 reliability closeout; P1 runnable mock
backend (P1a HTTP CRM adapter + independent simulator, P1b persistent
worker/heartbeat/seed/Compose); P2 business operations and queries
(contact-version coordination A32, enquiry/task lists, event timeline
A33, owner actions, integration status/resume, worker health); P3 intake
security (exact Origin, body-size bound, rate limits, trusted proxy,
signed webhook); P4 core UI and template drafts; P5 AI extraction and
fallback; P6 controlled failure demonstration; P7 connected verification
and handover. UI is P4 and intake security is P3, never P2.

1. **P0 (PR 1)**: reliability closeout, regression pins, status docs.
2. **P1a (PR 2, this branch)**: HTTP CRM adapter + independent provider
   simulator, proven over real HTTP with no hidden write retries.
3. **P1b (PR 3)**: persistent worker, heartbeat, seed, Compose.
4. **P2**: business operations and queries (A32, enquiry/task lists,
   event timeline A33, owner actions, integration status/resume,
   worker health).
5. **P3**: intake security (exact Origin, body bound, rate limits,
   trusted proxy, signed webhook).
6. **P4**: core UI and template drafts. **P5**: AI extraction and
   fallback. **P6**: controlled failure demonstration. **P7**:
   connected verification and handover.

Historical P1a note (2026-10-26): P1b-P7 were still missing then.
As of 2026-11-01 all of P1b, P2, P3, P4, P5 and P6 are implemented
on local `main`; P7 connected capability is classified four ways in
`docs/connected-handover.md`: implemented and verified (mock CRM
adapter vs provider-sim, fixture/template extraction), implemented
but unverified (HTTP CRM adapter wire behaviour against a real
provider), not implemented (live extractor for connected mode; real
provider conformance), and excluded by scope (real HubSpot/LLM calls
in this delivery); M0 remains open. No real CRM is called; no
acceptance item is signed off in code (operator-kept record).

## P1b-2 verification (2026-10-26, local only, not CI)

- Suites at the P1b-2 revision: 57 unit + 155 integration green on
  real PostgreSQL (one known Starlette/httpx warning); 0 leftover
  leadops_test_% databases and 0 test connections. `uv lock --check`
  passes after adding uvicorn via `uv lock` (cache redirected into
  the project venv); CI's `uv sync --locked` was not run locally.
- Docker Compose (isolated project p1b2verify, clean volume, then
  removed; the normal db service was restored afterwards):
  `docker compose config` OK; migrate+seed exited successfully;
  api/provider-sim/db healthy; login 200 -> submit 202 -> detail
  ready/synced (remote_id sim-*); worker logs show the simulator
  reached at provider-sim:8091 (GET 404 then POST 201). Worker
  restarted, new work after idle processed, and an armed one-shot
  lookup 500 produced waiting_retry then attempt 2 succeeded.
  Provider-sim is expose-only (never published); the API is bound
  to 127.0.0.1:8080 only.
- The throwaway cross-process scripts used for this evidence are
  intentionally not committed; this record is their result.
