# Demo evidence (synthetic data, isolated stack)

Executed 2026-11-03 (UTC) against an isolated Compose project
`eqdemo` on a fresh volume, code at `fe90285` (current `main`).
All data is synthetic; the provider is the local simulator.
Nothing here is connected evidence or an acceptance sign-off.
The demo project and its volume were removed after the run
(`docker compose -p eqdemo down -v`); the working stack was not
touched. Steps are in `docs/demo-runbook.md`.

## Scenario 1: normal enquiry to synced

- Submitted `demo-one@example.com` -> `ENQ-000001`
  (`b946c93e-1c1b-4249-ae8c-496faaa8822a`).
- Detail reached `ready`, attention `none`, CRM `synced`, owner
  `Oliver Reed`, follow-up task present.
- Simulator: exactly 1 contact for the email
  (`sim-dc02010be89c`).

## Scenario 2: lost create response, readback recovery

- Fault armed: `error_after_commit` on `create` (simulator
  commits, then errors the response).
- Submitted `demo-two@example.com` -> `ENQ-000002`
  (`a285188d-b950-44a9-a978-ba61a2f28651`).
- Recovered without operator action to `ready` / `synced`.
- Simulator: exactly 1 contact for the email
  (`sim-2b59531e7662`); no duplicate create.

## Scenario 3: auth block, probe, resume

- Fault armed: `http_401_before_write` on `create`.
- Submitted `demo-three@example.com` -> `ENQ-000003`
  (`e4e252f1-c425-48a5-93c6-a138b87daa32`).
- `GET /api/v1/integrations`: `hubspot` = `blocked`,
  `last_error_code = auth_failed`.
- First resume attempt returned 422 (`body.reason` required);
  the runbook now documents the required `reason` field.
- Resume with reason after disarming the fault: 200, provider
  back to `ok`; enquiry drained to `ready` / `synced`.
- Simulator: exactly 1 contact for the email
  (`sim-aba33f69507d`).

## Scenario 4: operator follow-up in the UI (recorded)

- Playwright (Chromium, `recordVideo`) drove the real UI:
  sign in, enquiries, detail with synthetic badge, tasks,
  complete the follow-up for `ENQ-000001` with a reason,
  completed filter, reload; the completed state survived.
- Result: `DEMO_RECORD_OK`; video
  `page@5f20ad555aee67d6018ed749f78e1a7c.webm`, 845,599 bytes,
  kept outside the repository (local demo artefact; it contains
  only synthetic data and the demo operator name, no secrets).
  The signed recording artefact for H07 remains the operator's
  to produce and review.

## Contract input search (D07, C01/C02)

Searches performed 2026-11-03 for the full authority package and
for the historical `LeadOpsKit-Demo1-Plan-v2.1-TDD.zip` lead:
repository plan package (partial update package only),
`D:\Projects` to depth 4 (zip filenames), user
Downloads/Documents/Desktop, user profile to depth 3
(`*LeadOps*`), `D:\` to depth 2, and the earlier
`D:\Projects\Upwork-Projects` depth-5 search for
`authority.md` / `openapi.yaml` / `vocabulary.yaml` /
`generate_vocabulary.py`. No candidate was found; the named zip
is not present in any searched location. A v2.1 archive, if
produced, would in any case predate the v3.0/v3.0.1 plan and
could not be promoted to current authority without the
superseding v3.0.1 files.

The single outstanding contract-input request lives in
`docs/acceptance-log.md` (C01/C02 rows); it is not duplicated
here. Until that input exists, D07 generation and the C01/C02
offline checks remain blocked and OPEN; Option B is not
implemented (no approval) and would not close C01/C02 in any
case.

## Recording delivery (2026-11-03)

The first demo recording (`page@5f20ad555aee67d6018ed749f78e1a7c.webm`,
845,599 bytes) was produced by an ad-hoc script and kept only in
a local temp directory; it is superseded as the deliverable by
the recording below and is not referenced as a delivered file.

Delivered recording, produced with the committed entry
(`e2e/demo.config.ts` + `e2e/demo.spec.ts`, video always kept)
against the `eqdemo` override stack (API 18080, UI 15173, main
stack verified healthy before and after), app code `fe90285`:

- File: `D:\Projects\Upwork-Projects\Project-Plans\EnquiryTrail\deliverables\demo\enquirytrail-demo-ui.webm`
  (outside the repository; not committed to Git).
- SHA-256: `2CAA2B6EF210D0EFBA175B5CAB1DCF975660979AAF4C7D9EBED87B9599ABEDED`
- Size: 362,622 bytes. Duration: 7 seconds. Resolution: 800x450.
- Covers: the UI flow only - sign in, synthetic quote, synced
  detail, follow-up completion, completed state after reload.
  Scenarios 1-3 above are API/simulator evidence and are not in
  this video; nothing here is a four-scenario recording.
- Playback was verified by actually opening the file in a
  desktop Chrome and inspecting frames at the start, middle,
  and end: quote form with synthetic data, detail page
  `ready (attention: none)` / CRM `synced`, and the completed
  follow-up under the Completed filter after reload. No
  password, `SESSION_SECRET`, or token is visible (sign-in
  typing is not shown; only synthetic data appears). The
  Playwright-bundled headless Chromium could not decode the
  WebM for this check, so verification used desktop Chrome;
  operators should open the same file the same way.
- This is an agent content check, not the operator's H07
  sign-off; H07 stays OPEN.

## Addendum: isolation fixes (2026-11-02)

- Rendered-config negative checks (shell placeholders and an
  isolated env file carrying https://crm.example.invalid,
  https://notify.example.invalid and fictitious tokens): after the
  compose.demo.yaml pinning, api/worker render CRM mock at
  http://provider-sim:8091, Slack disabled with empty URL/token,
  SIM_TOKEN equals the demo adapter token (boolean check only),
  ports 18080/15173 only, db unpublished, no placeholder value in
  the rendered output. Before the fix the same placeholders were
  inherited by api/worker (CRM http at the external URL, Slack
  http at the external URL).
- Playwright collection: before, the normal config listed 8 tests
  including demo.spec.ts (as CI run 41585673556 executed); after
  adding testIgnore for demo.spec.ts the normal config lists
  exactly the original 7 core-ui tests and demo.config.ts lists
  exactly the 1 recording test.
- Minimal smoke on the pinned config: demo API live ok, UI 200,
  synthetic intake 202 ENQ-000001, enquiry reached crm status
  synced with a sim-* remote id, main stack healthy before and
  after; the eqdemo project and volume were removed.
