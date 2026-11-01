# E2E browser evidence

This document records a real Playwright verification of the signed-in
browser flow against the real stack (API, worker, provider simulator,
database, and web). It supports acceptance item A28. It does not sign
off any acceptance item; acceptance remains the operator's record.

## Branch and worktree

- Branch: `feat/e2e-browser-verification`
- Worktree: `.worktrees/wt7`
- Base at recording time: `cecf734`. Measured at base `a305db162285f5c65e1468ca563480b900587dac` on 2026-11-01 (docs re-verification, not a re-run): that base was the measured commit; this docs branch and subsequent local merges stay unpushed until the final push.
- Test commit: `1b3d3df` (`test(e2e): add signed-in browser verification against the real stack`)

## Changed files

- `e2e/core-ui.spec.ts` (extended; no existing assertion weakened or deleted)
- `e2e/playwright.config.ts` (new)
- `e2e/package.json` (new)
- `e2e/package-lock.json` (new)
- `docs/e2e-browser-evidence.md` (this file)

No files under `backend/`, `frontend/src`, `AGENTS.md`,
`docs/development-plan.md`, `docs/contract-deviations.md`, or
`docs/connected-handover.md` were changed.

## Environment

- Docker Desktop daemon was available (`docker version` server 29.9.2).
- Isolated Compose project: `wt7e2e` (own volume `wt7e2e_leadops_db_data`).
- Throwaway local values were passed through the environment only and
  were never written to a tracked file:
  - `SESSION_SECRET` (local throwaway value)
  - `SEED_OPERATOR_PASSWORD` (local throwaway value)
  - `SEED_OPERATOR_USERNAME=demo-operator`
  - `ALLOWED_ORIGINS=http://127.0.0.1:5173` (required so the browser
    origin on port 5173 passes the exact-Origin ingress check; the API
    default derives the allowed origin from `APP_BASE_URL`, port 8080)
- Playwright credentials came from the environment only:
  `E2E_USERNAME=demo-operator`, `E2E_PASSWORD` set to the seed value,
  `E2E_BASE_URL=http://127.0.0.1:5173`.
- Browser: real Chromium via Playwright 1.64.1
  (`npx playwright install chromium` equivalent already present).

## Exact commands

```powershell
# From the workspace root (worktree was already created for this branch):
git worktree list   # .worktrees/wt7 at cecf734 on feat/e2e-browser-verification

# Inside .worktrees/wt7, with the throwaway environment values above set:
docker compose -p wt7e2e down -v          # clean isolated volume first
docker compose -p wt7e2e up -d --build
docker compose -p wt7e2e ps               # db, provider-sim, api healthy; web and worker up
curl.exe -s http://127.0.0.1:8080/api/v1/health/live   # {"status":"ok"}
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:5173/   # 200

$env:E2E_USERNAME='demo-operator'
$env:E2E_PASSWORD='<seed value from environment>'
$env:E2E_BASE_URL='http://127.0.0.1:5173'
npx --prefix e2e playwright test --config e2e/playwright.config.ts

npm --prefix frontend run test
npm --prefix frontend run build
# Backend unit tests (backend untouched; run with the main checkout venv):
#   backend\.venv\Scripts\python.exe -m pytest tests/unit -q   (from backend/)

docker compose -p wt7e2e down -v
docker ps -a   # no wt7e2e containers remain
docker volume ls   # no wt7 volume remains
```

## Observed results

### Playwright (final run, real Chromium, real stack)

```text
Running 5 tests using 1 worker
  ok 1 login page has labelled fields and synthetic badge
  ok 2 six core routes are reachable after sign-in
  ok 3 signed-in flow: quote to enquiries to detail to completed task survives reload (11.2s)
  ok 4 polling pauses while the page is hidden and resumes when visible (12.9s)
  ok 5 draft edit is retained after a forced 409 conflict (1.3s)
  5 passed (26.7s)
```

Counts: 5 passed, 0 failed, 0 skipped.

Flow covered through labelled UI controls only:

1. Signed in as the seeded demo operator.
2. Submitted a quote through the Quote page (including a postcode, so
   the enquiry does not require review for `missing_postcode`).
3. Saw the enquiry (for example `ENQ-000001`) appear in the Enquiries list.
4. Opened its detail, confirmed the synthetic-data badge and the detail
   reaching `ready (attention: none)` with CRM `synced` via polling.
5. Completed the follow-up task from the Tasks page with a valid reason.
6. Confirmed the completed state under the Completed filter and that it
   survives a reload.
7. Polling pause: with the page hidden, no `/api/v1/enquiries` polling
   requests were observed for 12 seconds; polling resumed when visible.
8. Draft 409: a second page in the same signed-in context saved the
   draft first; the first page's stale-version save then returned an
   alert and the typed subject and body were retained in the form.

### Earlier runs in this session (honest record)

- First run on the reused volume: 2 passed, 3 failed at sign-in with
  `Origin is not allowed`. Cause: the isolated stack had been started
  without `ALLOWED_ORIGINS`, so the API only allowed the port-8080
  origin. Fixed by environment only (see above) and a clean volume.
- Second run: 4 passed, 1 failed. The signed-in flow reached
  `ready (attention: review_required)` with CRM `synced` because the
  quote had no postcode. Fixed in the spec by filling the labelled
  Postcode field; no application behaviour was changed.

### Other gates

- `npm --prefix frontend run test`: 2 test files passed, 14 tests passed.
- `npm --prefix frontend run build`: passed (`tsc -b && vite build`).
- Backend unit tests at recording time: 136 passed (`tests/unit -q`). Backend untouched. (Superseded count: 156 unit / 270 integration observed 2026-11-01 in `docs/acceptance-log.md`.)

## Artefacts (local only, not committed)

- HTML report: `.worktrees/wt7/playwright-report/index.html`
- Test results and traces (retained on failure):
  `.worktrees/wt7/test-results/`

These paths are referenced here only; `playwright-report/` and
`test-results/` are not committed.

## Teardown confirmation

- `docker compose -p wt7e2e down -v` completed: all `wt7e2e` containers,
  the `wt7e2e_default` network, and the `wt7e2e_leadops_db_data` volume
  were removed.
- Verified afterwards: no `wt7e2e` containers or volumes remain.
- Normal development services were untouched: the pre-existing
  `enquirytrail-db-1` container remained in its prior exited state and
  no normal-project service was started, stopped, or modified.

## Notes

- Credentials were supplied only through environment variables. No
  secret, `.env` file, `node_modules`, `dist`, `test-results`, or
  `playwright-report` content was committed.
- No acceptance item is signed off by this document.
