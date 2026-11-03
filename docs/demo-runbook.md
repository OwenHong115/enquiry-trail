# Demo runbook (synthetic data, mock stack)

Audience: the operator demonstrating EnquiryTrail to a reviewer.
Everything here runs against the local Compose stack with the
provider simulator. All data is synthetic; no real CRM, LLM, or
remote contact is touched, and nothing in this runbook is connected
evidence or an acceptance sign-off.

## Setup (isolated stack)

Use a throwaway Compose project so the demo never touches the
working stack or its data:

```powershell
$env:ALLOWED_ORIGINS = "http://127.0.0.1:5173"
docker compose -p eqdemo up -d --build
# seed creates exactly one operator: demo-operator with the
# SEED_OPERATOR_PASSWORD from your local .env (never commit it)
curl.exe http://127.0.0.1:8080/api/v1/health/live
# open http://127.0.0.1:5173 and sign in as demo-operator
```

When finished: `docker compose -p eqdemo down -v` removes only the
demo project and its volume.

Faults are injected through the simulator's test endpoint inside
its container (the simulator port is not published):

```powershell
# set a one-shot fault:  docker compose -p eqdemo exec -T provider-sim python -c "<post /__test__/fault>"
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

## Recording the demo

Option A (browser video, no extra tooling): run the UI scenarios
through Playwright with video enabled and keep the `.webm`:

```powershell
# in e2e/, with the demo stack running and E2E_USERNAME /
# E2E_PASSWORD set for demo-operator:
npx playwright test --project=chromium
# videos are written under test-results/ (gitignored)
```

Option B (screen recording): record the browser window with the
OS recorder (Windows: `Win+Alt+R`) while stepping through
scenarios 1-4 above. Before sharing, scrub the recording: it
must not contain the operator password, `SESSION_SECRET`, or any
non-synthetic personal data. Recordings live outside the
repository; the checklist item that asks for one is signed off
by the operator, not by this runbook.

## What this demo does not prove

- No real provider behaviour: the simulator is a local wire
  stand-in (see `docs/connected-handover.md`; connected mode is
  UNVERIFIED and the live extractor is not implemented).
- No acceptance: every item in `docs/acceptance-log.md` stays
  `PENDING OPERATOR` until the operator signs it.
- Contract checks C01/C02 are unrelated to this demo and remain
  blocked on the full authority package.
