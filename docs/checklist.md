# Handover checklist (H01-H07)

Source: the plan definition of done (plan section 9, P7 exit gate:
"H01-H07 with actual evidence"). The full plan package lives outside
this repository and is not vendored here, so each item below is worded
from that definition of done and carries either a real artefact
inspected in this worktree or the exact text `OPEN` plus what closes
it. Nothing is marked complete without an inspected artefact.

Worktree: `.worktrees/wt29`, branch `docs/audit2-truth-closeout`.
Measured at base `e04ef02` on 2026-11-02 (real passed runs): backend unit
167 passed, backend integration 273 passed, frontend 17 passed. Playwright live at
the `e04ef02` stack: 7 passed (see `docs/e2e-browser-evidence.md`).
Push status, anchored to recorded facts only: remote `main` was `8dafed9` on 2026-11-02 (operator-supplied; see the CI run below). PRs #1-#8 are merged. Local commits after `8dafed9` are not claimed as pushed.

| ID | Handover item | Evidence | Status |
|---|---|---|---|
| H01 | Applicable demo-priority acceptance items evidenced before recording | `docs/acceptance-log.md` maps A01-A33 and C01/C02 to tests, commits, and observed runs (re-verified 2026-11-02: unit 167 passed, integration 273 passed, frontend 17 passed; base e04ef02). A19/A20 have Slack automated evidence; A26 has automated process-level evidence plus a compose-form live record (2026-11-02 PASS); C01/C02 are an offline contract-check gap (blocked on the missing full authority package; see H04), and A30 is the separate live-provider verification gap (not run). Either gap keeps this gate from closing on automation alone. | OPEN - closes when the operator reviews `docs/acceptance-log.md`, runs or accepts the demo flow (including the A28 recording), and records verdicts. No verdict is pre-filled. |
| H02 | Ship-gate items (A13/A14/A15/A18/A22/A25/A26/A32/A33) complete with the phase that introduced the behaviour; a process-exit test is not replaced by an in-process exception | A13/A15/A18/A32/A33 have passed integration evidence (273 passed, 2026-11-02, base e04ef02). A25 has full evidence including the API 503 mapping (`IntakeUnavailableError`, narrowed: `OperationalError`/`InterfaceError` or `DBAPIError` with `connection_invalidated`; `ProgrammingError`/`IntegrityError` stay 500). A14 has real subprocess exit evidence (`os._exit(97)` child); A22 has frontend escaped-rendering cases, backend intake storing the message as data, plus the dedicated e2e injection case in `e2e/core-ui.spec.ts` (`A22: untrusted enquiry text renders escaped with no external requests`; asserts escaped literal render, no injected script/img execution, zero non-local requests; Playwright 7 passed live at the `e04ef02` stack, re-verified 2026-11-02); A26 has automated process-level evidence (`backend/tests/integration/test_a26_process_recovery.py`) plus a compose-form live record (2026-11-02 PASS, `docs/a26-compose-recovery-evidence.md`). | OPEN - closes with operator review of the recorded A22 evidence (frontend escaped-rendering cases, storage-as-data, and the dedicated e2e injection case) and operator review of the recorded A26/A14 process-exit evidence, each recorded with counts. (The dedicated A22 e2e injection case already exists and the A25 API 503 mapping is done; neither is a closing condition.) |
| H03 | Merge gate: CI green for the current PR head (or its tested integration with current main), review findings resolved, contract changes reviewed | Historical baseline: GitHub run [41403905282](https://github.com/OwenHong115/enquiry-trail/actions/runs/41403905282) on `main` at `8dafed9` succeeded in all four jobs (test, frontend, lint, e2e) - that run proves `8dafed9`, not any later branch. Current branch `fix/round3-closeout`: PR #9 head `ff4ce1b` CI run [41456429718](https://github.com/OwenHong115/enquiry-trail/actions/runs/41456429718) succeeded in all four jobs (test, frontend, lint, e2e), observed 2026-11-02. Docs-only follow-up commits on the same PR are covered by their own PR runs. Local suites at base `e04ef02` (2026-11-02: unit 167 passed, integration 273 passed, frontend 17 passed) are local evidence, not CI. | OPEN - closes when (1) CI for the current PR head (or its tested merge with current `main`) is observed green, (2) review findings are resolved and recorded, and (3) contract changes are reviewed and recorded. |
| H04 | Contract checks C01 (enum copies consistent) and C02 (fixtures validate) pass | C01/C02 are OFFLINE contract checks. They are blocked on the missing full authority package; they are not live-provider evidence. Missing files: authority.md, openapi.yaml, vocabulary.yaml, generate_vocabulary.py, and the contract fixtures. The repository plan package is the partial update package (only PLAN.md, contracts/tdd.yaml, contracts/verification.yaml, docs/build-layout.md, CHANGELOG.md, plus APPLY-STATUS.txt noting the original EnquiryTrail-plan directory rename and the absent leadops parent; D:/Projects/Upwork-Projects/Project-Plans/leadops does not exist). `docs/acceptance-log.md` records C01/C02 as NO AUTOMATED EVIDENCE. | OPEN - closes when the full authority package is supplied, C01/C02 are run offline against it, and the outputs are recorded by the operator. |
| H05 | This checklist (H01-H07) complete with evidence fields filled in | This file is on current `main`/docs state (see git log for `docs(checklist):` / `docs(acceptance):` commits), but H01-H04, H06, and H07 remain open, so the pack as a whole is not complete. | OPEN - closes only when every other row carries an inspected artefact and the operator accepts the pack. |
| H06 | Stack starts from a clean volume following the README/AGENTS quick start; A30 real smoke recorded, or connected mode explicitly described as unverified | Connected half is satisfied as a statement: `docs/connected-handover.md` states connected mode is UNVERIFIED, M0 O01-O04 open, mock-labelled delivery. Clean-volume evidence exists: `docs/h06-clean-volume-evidence.md` records the 2026-11-02 run at `e04ef02` (fresh-volume `docker compose -p eqclean up -d --build` -> migrate+seed created exactly 1 operator, api healthy, web 200, login 200; then `down -v` and the main stack was restored). A30 was not run (no real account). | OPEN - the clean-volume evidence is recorded and complete; the closing condition is operator sign-off only. The completed clean-volume run is not to be re-run as a closing condition. |
| H07 | UI mode matches actual configuration; any recording contains no secrets; branch pushed state known | A full synthetic demo was executed on an isolated stack and the UI flow was recorded with the committed demo entry: `docs/demo-runbook.md` + `docs/demo-evidence.md` (2026-11-03; delivered video outside the repository with SHA-256 and frame inspection recorded there). UI/configuration match is also supported by `docs/e2e-browser-evidence.md` (real stack, synthetic badge, throwaway environment values never committed). Push status, anchored to recorded facts only: remote `main` was `8dafed9` on 2026-11-02 (operator-supplied). Local commits after `8dafed9` are not claimed as pushed. | OPEN - closes when the operator confirms the UI mode against configuration, reviews any recording for secrets, and records the branch state. |

## Known open items (must stay open until closed by an artefact)

- Connected mode unverified: M0 O01-O04 (HubSpot token scope,
  visibility after a lost write, patch with empty-field preservation,
  LLM output interface) - see `docs/connected-handover.md`.
- Operator acceptance sign-offs: every verdict in
  `docs/acceptance-log.md` is `PENDING OPERATOR`.
- Historical CI baseline: GitHub run 41403905282 on remote
  `main` at `8dafed9` succeeded in all four jobs (see Current
  status, 2026-11-02). It proves `8dafed9` only.
- Current branch `fix/round3-closeout`: PR #9 head `ff4ce1b`
  CI run 41456429718 succeeded in all four jobs (observed
  2026-11-02). H03 stays OPEN until its review conditions
  (findings resolved/recorded, contract changes reviewed) are
  recorded by the operator.
- Pushed state, anchored to recorded facts only: remote `main`
  was `8dafed9` on 2026-11-02 (operator-supplied). Local commits
  after `8dafed9` are not claimed as pushed.

## Current status (2026-11-02)

Historical CI baseline (operator-supplied, 2026-11-02): run [41403905282](https://github.com/OwenHong115/enquiry-trail/actions/runs/41403905282) on remote `main` at `8dafed9` succeeded in all four jobs: test (unit 167 passed; integration 273 passed, 1 warning), frontend (17 passed, build ok), lint (ruff and eslint ok), e2e (Playwright 7 passed). That run uploaded no artifacts. PRs #1-#8 are merged with no inline review threads; that is a statement of thread state, not a review approval. `main` branch protection: `protected=false`, no required status checks; a rulesets query returned a plan-limit 403, so ruleset state could not be confirmed.

- H03: the historical run above proves `8dafed9` only. For the
  the round-3 branch, PR #9 was operator-merged as `fe90285`
  and the landed commit's run 41577712532 was observed green
  in all five jobs (2026-11-03); the review findings behind
  that PR are fixed and recorded. H03 stays OPEN only for the
  operator's closure record, and D07/C01/C02 remain OPEN for
  their own contract-input reasons (H04) - they are not hidden
  by this status.
- Branch protection on `main`: `protected=false`, no required
  status checks. A rulesets query returned a plan-limit 403, so
  ruleset state could not be confirmed. Recommended settings are in
  `docs/github-branch-protection.md`; nothing has been applied.
- C01/C02 are OFFLINE contract checks blocked on the missing full
  authority package (missing: authority.md, openapi.yaml, vocabulary.yaml, generate_vocabulary.py, and the contract fixtures). The repository plan package is the partial update package (only PLAN.md, contracts/tdd.yaml, contracts/verification.yaml, docs/build-layout.md, CHANGELOG.md, plus APPLY-STATUS.txt noting the original EnquiryTrail-plan directory rename and the absent leadops parent; D:/Projects/Upwork-Projects/Project-Plans/leadops does not exist).
- H06: clean-volume evidence exists
  (`docs/h06-clean-volume-evidence.md`); the closing condition is
  operator sign-off only.
- Every acceptance verdict stays `PENDING OPERATOR`.
