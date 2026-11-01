# Development plan — post-P0 snapshot

## Snapshot (2026-10-26)

- Snapshot below is historical (2026-10-26). Current truth at docs
  re-verification 2026-11-01: local `main` and this branch HEAD are
  `a305db162285f5c65e1468ca563480b900587dac`, `origin/main` is
  `5872be3a1a7a0ab6754ea4fea472d53beea3d39a` (0 behind, 26 ahead,
  unpushed). Suites then: backend unit 156 passed, backend
  integration 270 passed, frontend 14 passed - see
  `docs/acceptance-log.md`.
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
on local `main`; P7 connected mode is implemented but UNVERIFIED
pending M0. No real CRM is called; no acceptance item is signed
off in code (operator-kept record).

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
