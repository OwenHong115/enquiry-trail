# Handover checklist (H01-H07)

Source: the plan definition of done (plan section 9, P7 exit gate:
"H01-H07 with actual evidence"). The full plan package lives outside
this repository and is not vendored here, so each item below is worded
from that definition of done and carries either a real artefact
inspected in this worktree or the exact text `OPEN` plus what closes
it. Nothing is marked complete without an inspected artefact.

Worktree: `.worktrees/wt8`, branch `feat/acceptance-handover-pack`,
base `d5b6777`. Suites observed here: backend unit 136 passed,
backend integration 234 passed (real PostgreSQL via compose `db`),
frontend 14 passed. Playwright is cited from
`docs/e2e-browser-evidence.md`, not re-run here.

| ID | Handover item | Evidence | Status |
|---|---|---|---|
| H01 | Applicable demo-priority acceptance items evidenced before recording | `docs/acceptance-log.md` maps A01-A33 and C01/C02 to tests, commits, and observed runs. Demo items without automated evidence (none of A19/A20/A26/A30 are demo, but A14 is only PARTIAL) keep the pack from closing this gate on automation alone. | OPEN - closes when the operator reviews `docs/acceptance-log.md`, runs or accepts the demo flow (including the A28 recording), and records verdicts. No verdict is pre-filled. |
| H02 | Ship-gate items (A13/A14/A15/A18/A22/A25/A26/A32/A33) complete with the phase that introduced the behaviour; a process-exit test is not replaced by an in-process exception | A13/A15/A18/A32/A33 have green integration evidence (234 passed). A14 and A25 are PARTIAL and A22 lacks a dedicated injection case; A26 (actual `docker compose kill` of the worker, then restart and count verification) has NO AUTOMATED EVIDENCE. | OPEN - closes with a real process-exit exercise for A26 (and the process half of A14), a dedicated untrusted-text case for A22, and the A25 API 503 mapping, each recorded with counts. |
| H03 | Merge gate: CI green for the current PR head (or its tested integration with current main), review findings resolved, contract changes reviewed | No artefact inspected. This sandbox has no `gh` CLI, so GitHub CI status for current `main` (`d5b6777`) cannot be verified from here. Local suites are green (unit 136, integration 234, frontend 14), which is local evidence, not CI. | OPEN - closes when CI reports green on the exact head to be merged and the review is recorded. Reason it stays open: no `gh` CLI in this sandbox and no CI artefact inspected. |
| H04 | Contract checks C01 (enum copies consistent) and C02 (fixtures validate) pass | No artefact in this repository: the vocabulary source, generator, fixtures, and OpenAPI authority live in the external plan package, which is not vendored here. `docs/acceptance-log.md` records C01/C02 as NO AUTOMATED EVIDENCE. | OPEN - closes when C01/C02 are run against the plan package artefacts and the outputs are recorded by the operator. |
| H05 | This checklist (H01-H07) complete with evidence fields filled in | This file exists at commit (see git log for `docs(checklist):` / `docs(acceptance):` on `feat/acceptance-handover-pack`), but H01-H04, H06, and H07 remain open, so the pack as a whole is not complete. | OPEN - closes only when every other row carries an inspected artefact and the operator accepts the pack. |
| H06 | Stack starts from a clean volume following the README/AGENTS quick start; A30 real smoke recorded, or connected mode explicitly described as unverified | Connected half is satisfied as a statement: `docs/connected-handover.md` states connected mode is UNVERIFIED, M0 O01-O04 open, mock-labelled delivery. Clean-volume start was not re-exercised in this worktree (only the compose `db` service was started for integration tests). A30 was not run (no real account). | OPEN - closes with a clean-volume `docker compose up -d --build` run recorded end to end, plus either an A30 live record or the operator's explicit acceptance of the unverified-connected statement. |
| H07 | UI mode matches actual configuration; any recording contains no secrets; branch pushed state known | UI/configuration match is supported by `docs/e2e-browser-evidence.md` (real stack, synthetic badge, throwaway environment values never committed). No recording was produced in this worktree. Push state: `main` is ahead of `origin/main` by 4 commits (the connected and e2e merges are local only) and `feat/acceptance-handover-pack` is not pushed, per the delivery instruction not to push. | OPEN - closes when the operator confirms the UI mode against configuration, reviews any recording for secrets, and decides the push/merge of the local-only merges. |

## Known open items (must stay open until closed by an artefact)

- Connected mode unverified: M0 O01-O04 (HubSpot token scope,
  visibility after a lost write, patch with empty-field preservation,
  LLM output interface) - see `docs/connected-handover.md`.
- Operator acceptance sign-offs: every verdict in
  `docs/acceptance-log.md` is `PENDING OPERATOR`.
- GitHub CI status for current `main`: unverifiable from this
  sandbox (no `gh` CLI); no CI artefact inspected.
- Pushed state: the newest merges on local `main` are ahead of
  `origin/main` and this branch is unpushed by instruction.
