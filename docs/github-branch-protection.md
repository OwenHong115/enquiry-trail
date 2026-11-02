# GitHub branch protection: recommended settings

Recommendation only, dated 2026-11-02. Nothing has been applied.
Applying these settings is an operator action in the GitHub UI;
this change applies nothing.

## Observed state (operator-supplied, 2026-11-02)

- Remote `main` was `8dafed9`. CI run
  [41403905282](https://github.com/OwenHong115/enquiry-trail/actions/runs/41403905282)
  on that SHA succeeded in all four jobs (test, frontend, lint,
  e2e) and uploaded no artifacts.
- PRs #1-#8 are merged, with no inline review threads. That is a
  statement of thread state, not a review approval.
- `main` branch protection: `protected=false`, no required status
  checks.
- A rulesets query returned a plan-limit 403, so ruleset state
  could not be confirmed from the API.

## Recommended settings for `main` (single-operator repository)

Apply in the GitHub UI (Settings, Branches, or Rulesets if the
plan allows it):

1. Require a pull request before merging into `main`.
2. Require status checks to pass before merging, with these four
   CI checks from `.github/workflows/ci.yml` as required:
   `test`, `frontend`, `lint`, `e2e`.
3. Require branches to be up to date before merging, so the
   required checks ran on the tested integration with `main`.
4. Block direct pushes to `main` (no bypass for administrators in
   normal use), so every change lands through a PR with green CI.
5. Required approvals: 0. This is a single-operator repository;
   requiring a human approver would block the operator's own PRs.
   The merge gate is green CI on the exact head, per
   `docs/development-process.md`. Manual acceptance signing stays
   with the operator and is not replaced by these settings.
6. Do not enable `continue-on-error` style bypasses in CI to make
   these checks pass; a red required check blocks the merge.

## Notes

- The rulesets plan-limit 403 observed on 2026-11-02 means the
  classic branch-protection UI may be the available route if
  rulesets are not on the current plan. Either route can express
  the settings above; pick the one the UI offers.
- These settings protect `main` only. Short-lived branches keep
  the workflow in `docs/development-process.md`.
- After applying, the operator should record the applied state
  (date and which settings) in `docs/checklist.md` under H03.
  Until then, H03 stays OPEN for its review conditions and no
  protection is claimed.
