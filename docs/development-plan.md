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

Still missing after P1a: P1b persistent worker/Compose and all of
P2-P7. Local P1a evidence: 55 unit + 143 integration green (includes 13 real-HTTP tests plus trickle-deadline unit tests); simulator
create counts prove one write/attempt. No real CRM is called; no
acceptance item is signed off in code (operator-kept record).
