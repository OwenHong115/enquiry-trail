# Handover checklist (H01-H07)

Source: the plan definition of done (plan section 9, P7 exit gate:
"H01-H07 with actual evidence"). The full plan package lives outside
this repository and is not vendored here, so each item below is worded
from that definition of done and carries either a real artefact
inspected in this worktree or the exact text `OPEN` plus what closes
it. Nothing is marked complete without an inspected artefact.

Worktree: `.worktrees/wt29`, branch `docs/audit2-truth-closeout`.
Measured at base `e04ef02` on 2026-11-02 (collect-only): backend unit
167, backend integration 273, frontend 17 passed. Playwright live at
the `e04ef02` stack: 7 passed (see `docs/e2e-browser-evidence.md`).
Push status, anchored to recorded facts only: on 2026-11-01 `main`
was pushed at `24d4aab` (`origin/main` == `24d4aab` then); this
round's merges are local on top and will be pushed after final
review. No current-tip count is claimed.

| ID | Handover item | Evidence | Status |
|---|---|---|---|
| H01 | Applicable demo-priority acceptance items evidenced before recording | `docs/acceptance-log.md` maps A01-A33 and C01/C02 to tests, commits, and observed runs (re-verified 2026-11-02: unit 167, integration 273, frontend 17). A19/A20 have Slack automated evidence; A26 has automated process-level evidence plus a compose-form live record (2026-11-02 PASS); A30/C01/C02 have no live-provider automated evidence, which keeps this gate from closing on automation alone. | OPEN - closes when the operator reviews `docs/acceptance-log.md`, runs or accepts the demo flow (including the A28 recording), and records verdicts. No verdict is pre-filled. |
| H02 | Ship-gate items (A13/A14/A15/A18/A22/A25/A26/A32/A33) complete with the phase that introduced the behaviour; a process-exit test is not replaced by an in-process exception | A13/A15/A18/A32/A33 have green integration evidence (273 collected, re-verified 2026-11-02). A25 has full evidence including the API 503 mapping (`IntakeUnavailableError`, narrowed: `OperationalError`/`InterfaceError` only; `ProgrammingError`/`IntegrityError` stay 500). A14 has real subprocess exit evidence (`os._exit(97)` child); A22 lacks a dedicated injection case; A26 has automated process-level evidence (`backend/tests/integration/test_a26_process_recovery.py`) plus a compose-form live record (2026-11-02 PASS, `docs/a26-compose-recovery-evidence.md`). | OPEN - closes with a dedicated untrusted-text case for A22 and operator review of the recorded A26/A14 process-exit evidence, each recorded with counts. (The A25 API 503 mapping is done and is not a closing condition.) |
| H03 | Merge gate: CI green for the current PR head (or its tested integration with current main), review findings resolved, contract changes reviewed | No GitHub Actions run artefact inspected. GitHub CI status for the current head cannot be verified from here, and H03 stays OPEN until the operator observes a GitHub CI run. Local suites observed 2026-11-02: unit 167, integration 273, frontend 17 (collect-only counts), which is local evidence, not CI. The CI e2e `ALLOWED_ORIGINS` issue is fixed (`.github/workflows/ci.yml` e2e job sets `ALLOWED_ORIGINS=http://127.0.0.1:5173`) and the same compose+e2e flow was replicated locally 7/7 at the `e04ef02` stack. Local lint note: `ruff check app tests` is clean after `37cfa17` (merged as `1a63d02`); verified in this worktree with PATH `ruff` 0.15.18 (`All checks passed!`). (`python -m ruff` is unavailable in the main-tree venv: No module named ruff; CI runs `uv run ruff check app tests` — see `.github/workflows/ci.yml` jobs test/lint/frontend/e2e.) H03 still OPEN only because no GitHub Actions run has been observed. | OPEN - closes when CI reports green on the exact head to be merged and the review is recorded. Reason it stays open: no `gh` CLI in this sandbox and no CI artefact inspected. |
| H04 | Contract checks C01 (enum copies consistent) and C02 (fixtures validate) pass | No artefact in this repository: the vocabulary source, generator, fixtures, and OpenAPI authority live in the external plan package, which is not vendored here. `docs/acceptance-log.md` records C01/C02 as NO AUTOMATED EVIDENCE. | OPEN - closes when C01/C02 are run against the plan package artefacts and the outputs are recorded by the operator. |
| H05 | This checklist (H01-H07) complete with evidence fields filled in | This file is on current `main`/docs state (see git log for `docs(checklist):` / `docs(acceptance):` commits), but H01-H04, H06, and H07 remain open, so the pack as a whole is not complete. | OPEN - closes only when every other row carries an inspected artefact and the operator accepts the pack. |
| H06 | Stack starts from a clean volume following the README/AGENTS quick start; A30 real smoke recorded, or connected mode explicitly described as unverified | Connected half is satisfied as a statement: `docs/connected-handover.md` states connected mode is UNVERIFIED, M0 O01-O04 open, mock-labelled delivery. Clean-volume run recorded 2026-11-02 at `e04ef02` (see `docs/h06-clean-volume-evidence.md`): fresh-volume `docker compose -p eqclean up -d --build` -> migrate+seed created exactly 1 operator, api healthy, web 200, login 200; then `down -v` and the main stack was restored. Evidence recorded; H06 stays OPEN pending operator sign-off. A30 was not run (no real account). | OPEN - closes with a clean-volume `docker compose up -d --build` run recorded end to end, plus either an A30 live record or the operator's explicit acceptance of the unverified-connected statement. |
| H07 | UI mode matches actual configuration; any recording contains no secrets; branch pushed state known | UI/configuration match is supported by `docs/e2e-browser-evidence.md` (real stack, synthetic badge, throwaway environment values never committed). No recording was produced in this worktree. Push status, anchored to recorded facts only: on 2026-11-01 `main` was pushed at `24d4aab` (`origin/main` == `24d4aab` then); this round's merges are local on top and will be pushed after final review. No current-tip count is claimed. | OPEN - closes when the operator confirms the UI mode against configuration, reviews any recording for secrets, and decides the push/merge of the local-only merges. |

## Known open items (must stay open until closed by an artefact)

- Connected mode unverified: M0 O01-O04 (HubSpot token scope,
  visibility after a lost write, patch with empty-field preservation,
  LLM output interface) - see `docs/connected-handover.md`.
- Operator acceptance sign-offs: every verdict in
  `docs/acceptance-log.md` is `PENDING OPERATOR`.
- GitHub CI status for current `main`: unverifiable from this
  sandbox (no `gh` CLI); no CI artefact inspected.
- Pushed state, anchored to recorded facts only: on 2026-11-01
  `main` was pushed at `24d4aab` (`origin/main` == `24d4aab` then).
  This round's merges are local on top and will be pushed after
  final review. No current-tip count is claimed.
