# Reliability closeout review — 2026-10-26 (Australia/Sydney)

Source baseline: original fix/reliability at 738a1d7. Work was performed in an
isolated source snapshot because the original Project-Plans path is outside this
session's writable roots. The original branch and remote PR were not modified.

## Implemented

- R17 process logging, repeatable configuration, preserved host capture, no duplicate
  console output for preconfigured host sinks; pool wait bound.
- HTTP retry discovery through related_jobs; dead/provider precedence and satisfied
  older enquiries; published schema types and common error/auth metadata.
- One claim-layer budget policy, with successful fourth-attempt coverage; unresolved
  intent normalization; create operation identity in CallContext; readback delay steps.
- New password work factor and matching dummy; compatibility verification tests.
- Actual deviation record and corrected PR description.

## Observed evidence

- 2026-10-26, on the closeout revision (652ffd7 + regression pins
  3e45814, branch fix/p0-closeout; git fetch origin confirmed main
  unmoved at 0156c67):
  - cd backend; .venv\\Scripts\\python.exe -m pytest tests/unit -q
    -> 45 passed in 1.55s
  - DATABASE_URL=postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops; .venv\\Scripts\\python.exe -m pytest tests/integration -q
    -> 130 passed, 1 warning in 111.63s (128 pre-existing + 2 new pins;
    the known Starlette/httpx deprecation warning only)
  - Afterwards: 0 leadops_test_% databases and 0 test connections.
- Regression pins (test_closeout_regressions.py): readback NotFound
  next_attempt_at exactly T+1, T+5, T+21; create CallContext
  external_operation_id equals the inserted external_operations id and
  the readback context carries the same id. Both fail when the
  pre-closeout behaviour (fixed 4s readback delay; create context
  without the operation id) is temporarily restored, and pass on the
  closeout revision.
- Earlier partial evidence from the source snapshot is superseded by
  the complete run above.

## Required before merge

1. Push fix/p0-closeout and obtain green CI (unit + real PostgreSQL
   integration) on the exact PR revision.
2. Review the final diff; merge to main. Do not mix the CRM adapter
   into this PR.

No remote CI result, production readiness, or manual acceptance signoff is claimed until CI reports on the pushed PR revision. Local merge gate: complete (see above). Remaining gate: green CI on the PR revision, then merge. Update 2026-11-01: this 2026-10-26 review is historical. The current gate is H03 in `docs/checklist.md` (OPEN until the operator observes a GitHub CI run for the exact head; `.github/workflows/ci.yml` now has test/lint/frontend/e2e jobs). Local suites at HEAD `a305db1`: unit 156, integration 270, frontend 14 - see `docs/acceptance-log.md`.
