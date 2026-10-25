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

## Delivery boundaries (not implemented features)

- No UI, list endpoints, real HTTP CRM adapter, production worker supervisor or
  provider simulator. The console worker configures logs then refuses to start
  without a real CRM adapter. The injectable runner is exercised with FakeCrm.
- Public deployment hardening (HTTPS cookie policy, origin policy, rate limiting,
  request-size controls) remains in S7e/S9. This PR is the local demo reliability
  slice, not authorization for a public production deployment.
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
- P0 lands as its own PR (fix/p0-closeout from 652ffd7) before any CRM adapter work; adapter and closeout are never mixed in one PR. Order is P0, P1a adapter+simulator, P1b worker/Compose, P2 UI/hardening.
