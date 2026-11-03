# Demo runbook (synthetic data, mock stack)

Audience: the operator demonstrating EnquiryTrail to a reviewer.
Everything here runs against the local Compose stack with the
provider simulator. All data is synthetic; no real CRM, LLM, or
remote contact is touched, and nothing in this runbook is connected
evidence or an acceptance sign-off.

## Setup (isolated stack, parallel to the working stack)

The demo stack uses the `eqdemo` Compose project plus the
`compose.demo.yaml` override, so it binds different loopback
ports and can run while the working stack stays up:

| Service | Working stack | Demo stack (`eqdemo`) |
| --- | --- | --- |
| API | 127.0.0.1:8080 | 127.0.0.1:18080 |
| UI | 127.0.0.1:5173 | 127.0.0.1:15173 |
| Database | 127.0.0.1:55432 | not published |

The override replaces the base port lists (`!override`), sets
`APP_BASE_URL=http://127.0.0.1:18080` and
`ALLOWED_ORIGINS=http://127.0.0.1:15173` for the demo API, and
gives the demo its own project volumes. Without the override the
same host ports would collide with the working stack; `-p` alone
does not change host ports. Verify the rendered ports before
starting (inspect only the `ports` fields; the full rendered
config contains secrets):

Never print the full rendered config: it contains secrets. Use
this filtered check instead (exit code checked, only ports and
booleans printed, credential agreement reported as true/false):

```powershell
$raw = docker compose -p eqdemo -f compose.yaml -f compose.demo.yaml config --format json
if ($LASTEXITCODE -ne 0) { throw "compose config failed" }
$cfg = $raw | ConvertFrom-Json
"api ports: $($cfg.services.api.ports.published -join ',')"
"web ports: $($cfg.services.web.ports.published -join ',')"
"db published: $([bool]$cfg.services.db.ports)"
"api crm local: $($cfg.services.api.environment.CRM_BASE_URL -eq 'http://provider-sim:8091')"
"worker crm local: $($cfg.services.worker.environment.CRM_BASE_URL -eq 'http://provider-sim:8091')"
"slack disabled: $($cfg.services.api.environment.SLACK_MODE -eq 'disabled')"
"sim/api token match: $($cfg.services.'provider-sim'.environment.SIM_TOKEN -eq $cfg.services.api.environment.CRM_TOKEN)"
docker compose -p eqdemo -f compose.yaml -f compose.demo.yaml up -d --build
curl.exe http://127.0.0.1:18080/api/v1/health/live
# open http://127.0.0.1:15173 and sign in as demo-operator
# (password: the SEED_OPERATOR_PASSWORD from your local .env)
curl.exe http://127.0.0.1:8080/api/v1/health/live   # working stack still healthy
```

`SESSION_SECRET` and `SEED_OPERATOR_PASSWORD` come from your
local `.env` (required; compose fails without them by design).
When finished, remove only the demo project and its volume; the
working stack is not touched:

```powershell
docker compose -p eqdemo -f compose.yaml -f compose.demo.yaml down -v
```

Faults are injected through the simulator's test endpoint inside
its container (the simulator port is not published):

```powershell
# set a one-shot fault:  docker compose -p eqdemo -f compose.yaml -f compose.demo.yaml exec -T provider-sim python -c "<post /__test__/fault>"
# read counters:         GET /__test__/counts
# contacts by email:      GET /__test__/contacts?email=<email>
```

The helper used in the evidence run posts
`{"mode": "<mode>", "path": "<path>"}` to
`http://127.0.0.1:8091/__test__/fault` from inside the container.
Faults are one-shot: the first matching call consumes the mode.

## Scenario 1: normal enquiry to synced

1. Sign in, open **New quote**, submit a synthetic enquiry
   (for example first name `Demo`, email
   `demo-one@example.com`, service `kitchen`, a message of at
   least 20 characters). Note the enquiry number (`ENQ-…`).
2. Open **Enquiries**: the row reaches `ready (attention: none)`.
3. Open the detail page: synthetic badge visible, CRM status
   `synced`, an owner is assigned, and the follow-up task exists.
4. Expected evidence: exactly one simulator contact for that
   email (`/__test__/contacts?email=demo-one@example.com` ->
   `count: 1`), and `crm_links` for the contact is `synced`.

## Scenario 2: lost create response, readback recovery

1. Arm the fault: mode `error_after_commit`, path `create`.
   The simulator commits the contact, then answers the create
   with an error, so the worker cannot know the write landed.
2. Submit a second synthetic enquiry (new email, for example
   `demo-two@example.com`).
3. Watch the detail page: the enquiry goes through a retry /
   reconciling state and returns to `ready (attention: none)`
   without operator action.
4. Expected evidence: still exactly one simulator contact for
   that email, its id equals the local `crm_links.remote_id`,
   and the timeline shows the recovery (no duplicate create).

## Scenario 3: auth block, probe, resume

1. Arm the fault: mode `http_401_before_write`, path `create`.
2. Submit a third synthetic enquiry (for example
   `demo-three@example.com`). Its sync fails with `auth_failed`;
   the provider is marked blocked and the enquiry waits.
3. Open **Integrations**: `hubspot` shows blocked with
   `last_error_code = auth_failed`. New syncs stay queued; this
   is the system protecting the provider, not data loss.
4. Disarm the fault (mode `none`), then resume from the
   Integrations page (or `POST /api/v1/integrations/hubspot/resume`
   with `expected_status: "blocked"`, the row's
   `expected_updated_at`, and a `reason` of 10-500 characters).
   The resume probes the provider first;
   only a successful probe requeues the blocked job.
5. Expected evidence: integration returns to `ok`, the enquiry
   drains to `ready (attention: none)` and syncs exactly once.

## Scenario 4: operator follow-up in the UI

1. Open **Tasks**, find the follow-up for the scenario 1 enquiry
   (rows are scoped `task-row-{enquiry id}`).
2. Enter a reason of 10-500 characters and choose
   **Complete follow-up for ENQ-…**.
3. Switch the task status filter to **Completed**: the completed
   task is listed under the same scoped row; reload the page and
   confirm the completed state survives.
4. Open the enquiry detail **Events** timeline and show the
   recorded events (`enquiry.received`, `enquiry.prepared`,
   `crm.synced`, the task completion). Preserved CRM fields, if
   any are shown, are masked (first character plus `***`).

## Recording the demo (scenario 4, UI video)

The regular Playwright suite (`playwright.config.ts`) keeps
video only on failure for CI diagnostics, so it cannot deliver a
successful demo recording. Use the dedicated demo entry instead,
which always keeps video and never runs in CI:

```powershell
cd e2e
npm ci
npx playwright install chromium
$env:E2E_USERNAME = "demo-operator"
$env:E2E_PASSWORD = "<the demo operator password from your local .env>"
$env:DEMO_BASE_URL = "http://127.0.0.1:15173"
npx playwright test -c demo.config.ts
# video: ../demo-results/demo-demo-quote-to-synced-enquiry-to-completed-follow-up/video.webm
```

`e2e/demo.spec.ts` drives the real UI: sign in, submit a
synthetic quote, watch it sync, open the detail timeline,
complete the follow-up, and confirm the completed state survives
a reload. It does not cover scenarios 1-3; those are
API/simulator evidence (see `docs/demo-evidence.md`), and this
video must not be described as a four-scenario recording. Copy
the `.webm` to the persistent delivery location named in
`docs/demo-evidence.md` (outside the repository) and record its
SHA-256 there. Before sharing, check the frames: the recording
must contain only synthetic data and no password, `SESSION_SECRET`,
or token. A screen recorder (for example Windows `Win+Alt+R`)
is an acceptable alternative; the signed recording artefact for
H07 remains the operator's to produce and review.

## What this demo does not prove

- No real provider behaviour: the simulator is a local wire
  stand-in (see `docs/connected-handover.md`; connected mode is
  UNVERIFIED and the live extractor is not implemented).
- No acceptance: every item in `docs/acceptance-log.md` stays
  `PENDING OPERATOR` until the operator signs it.
- Contract checks C01/C02 are unrelated to this demo and remain
  blocked on the full authority package.
