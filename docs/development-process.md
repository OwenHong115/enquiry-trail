# Development process

How work is done on this repository. It replaces the earlier rule that every slice had to be
two commits, one `test(...)` and one `feat(...)`. That rule was useful for one audit series
and is not the default here any more.

## Test-first, not commit-first

Every behaviour change starts with a test that fails for the right reason, then the change
that makes it pass, then refactoring under that test. The loop is about feedback, not about
Git history: it does not require the failing state to be its own commit.

What must be recorded for a behaviour defect:

- what failed before the fix, quoted from a real run;
- what the fix verifies, quoted from a real run after it;
- which test reproduces the original defect.

The failing state may be committed, kept in the working tree, or simply reported. It is
evidence, not a delivery unit.

## One commit, one complete logical change

A commit contains everything one change needs: implementation, its tests, and any doc or
config change that belongs to it. If a commit is reverted, the repository must not be left
with a failing test or an untested behaviour.

Name the commit for what changed:

    fix(worker): close exhausted retry cycles at claim
    fix(logging): emit structured logs from application entrypoints
    feat(jobs): allow operators to retry terminal jobs
    feat(detail): expose related jobs for HTTP retry discovery

`test(...)` is not a commit type for new work: it says when in the cycle something happened,
not what changed. `feat` and `fix` describe the change; `chore` and `docs` are for work that
carries no behaviour.

## Changing existing tests

Tests are not frozen. A test may change when:

- the contract changes and the old assertion was correct for the old contract;
- the old assertion was wrong - for example it pinned a placeholder the contract never
  required;
- the assertion coupled to an implementation detail that a legitimate refactor moved.

The reason goes in the commit body and in the report. Deleting or weakening a valid
behavioural constraint to make a suite green is not allowed: stop and report the exact line
instead.

## Branches and pull requests

- One short-lived branch per logical change or per small feature, cut from an up-to-date
  `main`, deleted after merge.
- A development branch may be red while it is in progress. Nobody merges a red branch.
- Default history for a small, coherent PR: **squash to one commit**. The pull request itself
  is the unit of review and of revert.
- A PR that carries several independent fixes may instead keep one commit per fix, each
  verifiable on its own. That is a choice about revert granularity, not about rigour.
- Regression-test-first findings and red/green evidence can stay in the branch and in the PR
  description as review material even when the merge squashes them away.

## Merge gate

The only gate is green CI on the exact commit that lands.

- A local run, an earlier commit's run, or a run on a different branch does not substitute.
- Both suites must pass against real PostgreSQL; nothing is skipped to make the gate pass.
- The PR description states what was verified and on which commit.
- Manual acceptance signing stays with the operator. Code never marks an acceptance item as
  passed on the operator's behalf.

## Review checklist

- The change is one logical unit; a revert would not leave half of it behind.
- A new behaviour has a test that would fail without the change.
- A changed expectation has a stated reason.
- No assertion was weakened, deleted or reordered to reach green.
- The public contract still holds: statuses, event shapes, error envelopes, and the
  published status vocabularies. Deviations go to `docs/contract-deviations.md`.
- Tests assert persisted facts and public outputs, not private call counts or SQL text.

## History note

The reliability series (`R1`-`R17`, merged in PR #1) deliberately kept a red commit before
each green one, because it was an audit of whether a test could reproduce a real defect and
whether the fix addressed it rather than mutating the assertion. Those commits stay as
evidence. This document governs work from here on; do not rewrite that history to match it,
and do not treat its commit shape as the expected shape for new work.
