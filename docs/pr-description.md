Reliability failures could wedge the queue, lose a remote-write intent on a crash,
or leave operators unable to recover failed work. This branch fixes those paths
before further feature work.

## Scope

- Durable CRM intent before create, readback-first recovery, and lease-fenced result
  transactions. Definite create-401 failures can recover after credentials change.
- Claim-owned cycle budgets: no fifth execution; exhausted work becomes dead while
  unresolved operations remain unknown. Operator retry opens one audited new cycle.
- Existing enquiry detail exposes related job IDs, states and update timestamps so
  retry works entirely over HTTP. Published status vocabulary and failure precedence
  cover shared contacts and dead local preparation.
- Intake contact upsert and event-key serialization; active-operator session checks;
  consistent replay classification, resolution notes and task actor identity.
- Required session secret, malformed CSRF handling, dummy-hash verification for absent usernames, and 600,000-iteration hashes for new passwords (legacy hashes verify).
- JSON logging configured by API and worker entrypoints, bounded pool/DB waits,
  explicit HTTP security/error metadata, test connection cleanup, PostgreSQL 16 CI.

## Non-goals

No UI, list endpoints, new CRM features, real CRM adapter or production deployment.
No external contract files are changed. Intentional differences and remaining
boundaries are recorded in docs/contract-deviations.md for the planned revision.

## Evidence and merge gate

See docs/closeout-review.md for the actual local evidence and limitations. Do not
interpret committed tests as passing results. Merge only after both unit and real
PostgreSQL integration suites are green on the exact final PR commit. No tests are
skipped or removed to meet that gate. Test timing/placeholder expectations changed
only to implement the operator-approved claim ownership and HTTP retry behavior.

## Rollback

There are no new database migrations. If merged as one merge commit, revert that
merge commit against its first parent. A squash merge is reverted as an ordinary
single commit. The previous password verifier reads stored iteration counts, so it
can verify new hashes after a code rollback.
