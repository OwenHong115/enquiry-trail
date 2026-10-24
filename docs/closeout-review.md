# Reliability closeout review — 2026-10-25 (Australia/Sydney)

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

- New status/logging tests before implementation: 3 failed for the intended behavior.
- After R17/detail implementation: `46 passed, 1 warning in 4.93s` (all then-current
  unit tests, HTTP retry test, and both R17 PostgreSQL tests).
- Budget/password tests before their implementation: `3 failed, 2 passed in 6.18s`;
  failures were handler-owned terminal timing and the old password work factor.
- A later full-suite run was interrupted during follow-up by automatic approval
  review, which reported unauthorized Visual Studio Telemetry. The Python runner
  denied subprocesses and non-loopback sockets, but the execution environment still
  reported the external activity. No further full suite was attempted.
- Partial output contained a failure at the old related_jobs=[] placeholder. Source
  inspection identified test_api_detail.py's obsolete assertion, now replaced with
  stronger actual-job shape, identity, terminal-state and timestamp checks. There
  is no final result for that run; the final budget/password/expectation changes
  have not been certified by a complete green run.
- One existing Starlette TestClient/httpx deprecation warning remains; dependencies
  were not changed to conceal it.

## Required before merge

1. Apply the patch series to the original baseline (or resolve against newer work).
2. Run both suites on the final commit in an environment where test execution does
   not trigger unauthorized telemetry; verify test-DB connections return to baseline.
3. Push that commit and obtain its real PostgreSQL CI result.
4. Use docs/pr-description.md to update the actual PR and review the final diff.

No remote CI run, production readiness, or manual acceptance signoff is claimed.
Implementation is ready for review; merge readiness remains blocked on these gates.
