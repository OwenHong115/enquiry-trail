# Development plan — post-P0 snapshot

## Snapshot (2026-10-26)

- `main` / `origin/main`: `0156c67` (Merge PR #1, fix/reliability R16).
  `git fetch origin` on 2026-10-26 confirmed main had not moved.
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

1. **P0 (this PR)**: land the reliability closeout, the two regression
   pins (readback ladder T+1/T+5/T+21; create/readback operation
   identity), and these status docs. Merge only with green CI
   (unit + real PostgreSQL integration) on the exact PR revision.
2. **P1a (next PR)**: HTTP CRM adapter (`backend/app/adapters/crm_http.py`)
   plus the independent provider simulator (`provider-sim/`), proven over
   real HTTP: success, definite failure, unknown write, readback. No
   hidden write retries. See `docs/crm-http-contract.md` in that PR.
3. **P1b (PR 3)**: persistent worker loop, heartbeat, seed data, Compose
   wiring.
4. **P2**: UI, list endpoints, webhook/origin/rate-limit hardening,
   contact-version coordination (A32), event timeline (A33).

Still missing after P0: the HTTP adapter, the simulator, the persistent
worker, and the UI. No real CRM is called; no acceptance item is signed
off in code (see `docs/acceptance-log.md`, operator-kept).
