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

Single outstanding input request (no repository change can
substitute for it): supply the current v3.0.1 (or later) full
authority contract package, read-only and outside this
repository, at a configurable path, containing at least
`authority.md`, `contracts/openapi.yaml`,
`contracts/vocabulary.yaml`, `generate_vocabulary.py`, and the
contract `fixtures/*.json` including the six `detail_*` state
fixtures. Until then D07 generation and the C01/C02 offline
checks remain blocked and OPEN; Option B is not implemented
(no approval) and would not close C01/C02 in any case.
