# Handover checklist (H01-H07)

Source: the plan definition of done (plan section 9, P7 exit gate:
"H01-H07 with actual evidence"). The full plan package lives outside
this repository and is not vendored here, so each item below is worded
from that definition of done and carries either a real artefact
inspected in this worktree or the exact text `OPEN` plus what closes
it. Nothing is marked complete without an inspected artefact.

Worktree: `.worktrees/wt15`, branch `docs/final-truth-closeout`,
HEAD `a305db162285f5c65e1468ca563480b900587dac`. Suites observed
2026-11-01: backend unit 156 passed, backend integration 270
passed (real PostgreSQL at 127.0.0.1:55432), frontend 14 passed,
frontend build passed, ESLint exit 0. Playwright is cited from
`docs/e2e-browser-evidence.md`, not re-run here.

| ID | Handover item | Evidence | Status |
|---|---|---|---|
| H01 | Applicable demo-priority acceptance items evidenced before recording | `docs/acceptance-log.md` maps A01-A33 and C01/C02 to tests, commits, and observed runs (re-verified 2026-11-01: unit 156, integration 270, frontend 14). A19/A20 now have Slack automated evidence; A14 is only PARTIAL and A26/A30/C01/C02 have no automated evidence, which keeps this gate from closing on automation alone. | OPEN - closes when the operator reviews `docs/acceptance-log.md`, runs or accepts the demo flow (including the A28 recording), and records verdicts. No verdict is pre-filled. |
| H02 | Ship-gate items (A13/A14/A15/A18/A22/A25/A26/A32/A33) complete with the phase that introduced the behaviour; a process-exit test is not replaced by an in-process exception | A13/A15/A18/A32/A33 have green integration evidence (270 passed, re-verified 2026-11-01). A25 now has full evidence including the API 503 mapping (`IntakeUnavailableError`). A14 is PARTIAL (job-lease crash evidence, not a process kill) and A22 lacks a dedicated injection case; A26 (actual `docker compose kill` of the worker, then restart and count verification) has NO AUTOMATED EVIDENCE (operator script `scripts/a26-process-recovery.ps1` exists, not an automated suite test). | OPEN - closes with a real process-exit exercise for A26 (and the process half of A14), a dedicated untrusted-text case for A22, and the A25 API 503 mapping, each recorded with counts. |
| H03 | Merge gate: CI green for the current PR head (or its tested integration with current main), review findings resolved, contract changes reviewed | No GitHub Actions run artefact inspected. GitHub CI status for the current head cannot be verified from here, and H03 stays OPEN until the operator observes a GitHub CI run. Local suites observed 2026-11-01 are green (unit 156, integration 270, frontend 14, frontend build, ESLint exit 0), which is local evidence, not CI. Local lint note: PATH `ruff` 0.15.18 reports 1 F401 at `backend/tests/integration/test_api_auth.py:251` on this head (`python -m ruff` is unavailable in the main-tree venv: No module named ruff); the CI lint job runs `uv run ruff check app tests` (see `.github/workflows/ci.yml` jobs test/lint/frontend/e2e), so that finding must be resolved before a green CI run can be expected. It is not fixed in this docs-only branch. | OPEN - closes when CI reports green on the exact head to be merged and the review is recorded. Reason it stays open: no `gh` CLI in this sandbox and no CI artefact inspected. |
| H04 | Contract checks C01 (enum copies consistent) and C02 (fixtures validate) pass | No artefact in this repository: the vocabulary source, generator, fixtures, and OpenAPI authority live in the external plan package, which is not vendored here. `docs/acceptance-log.md` records C01/C02 as NO AUTOMATED EVIDENCE. | OPEN - closes when C01/C02 are run against the plan package artefacts and the outputs are recorded by the operator. |
| H05 | This checklist (H01-H07) complete with evidence fields filled in | This file exists at commit (see git log for `docs(checklist):` / `docs(acceptance):` on `feat/acceptance-handover-pack`), but H01-H04, H06, and H07 remain open, so the pack as a whole is not complete. | OPEN - closes only when every other row carries an inspected artefact and the operator accepts the pack. |
| H06 | Stack starts from a clean volume following the README/AGENTS quick start; A30 real smoke recorded, or connected mode explicitly described as unverified | Connected half is satisfied as a statement: `docs/connected-handover.md` states connected mode is UNVERIFIED, M0 O01-O04 open, mock-labelled delivery. Clean-volume start was not re-exercised in this worktree (only the compose `db` service was started for integration tests). A30 was not run (no real account). | OPEN - closes with a clean-volume `docker compose up -d --build` run recorded end to end, plus either an A30 live record or the operator's explicit acceptance of the unverified-connected statement. |
| H07 | UI mode matches actual configuration; any recording contains no secrets; branch pushed state known | UI/configuration match is supported by `docs/e2e-browser-evidence.md` (real stack, synthetic badge, throwaway environment values never committed). No recording was produced in this worktree. Push state measured 2026-11-01: HEAD `a305db162285f5c65e1468ca563480b900587dac`, `origin/main` `5872be3a1a7a0ab6754ea4fea472d53beea3d39a`, `git rev-list --left-right --count origin/main...HEAD` -> `0 26` (0 behind, 26 ahead). Local merges are unpushed until the final push; this docs branch is also unpushed. | OPEN - closes when the operator confirms the UI mode against configuration, reviews any recording for secrets, and decides the push/merge of the local-only merges. |

## Known open items (must stay open until closed by an artefact)

- Connected mode unverified: M0 O01-O04 (HubSpot token scope,
  visibility after a lost write, patch with empty-field preservation,
  LLM output interface) - see `docs/connected-handover.md`.
- Operator acceptance sign-offs: every verdict in
  `docs/acceptance-log.md` is `PENDING OPERATOR`.
- GitHub CI status for current `main`: unverifiable from this
  sandbox (no `gh` CLI); no CI artefact inspected.
- Pushed state: local `main`/HEAD is 26 ahead, 0 behind
  `origin/main` (measured 2026-11-01); local merges stay unpushed
  until the final push, and this branch is unpushed.
