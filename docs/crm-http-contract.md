# CRM HTTP wire contract (local, minimal)

No complete external wire contract exists in this repository, so this
is the minimal contract implemented between
`backend/app/adapters/crm_http.py` and `provider-sim/`. It is not a
claim of conformance to any external provider (no real CRM is called).

## Endpoints (simulator base = CRM_BASE_URL)

| Port method | Wire | Success | Not found |
|---|---|---|---|
| `get_contact_by_email` | `GET /contacts/by-email/{email}` | 200 contact | 404 -> `NotFound` |
| `get_contact_by_id` | `GET /contacts/{id}` | 200 contact | 404 -> `NotFound` |
| `create_contact` | `POST /contacts` | 201 contact | 404 -> `not_found` |
| `patch_contact` | `PATCH /contacts/{id}` | 200 contact | 404 -> `not_found` |
| `probe` | `GET /probe` (also `/health`) | 200 `{"ok":true,"supports_patch":true}` | failures -> `ProbeResult(ok=False)` |

Contact payload: `{"id", "email", "firstname", "lastname", "phone"}`.
Create sends only `email`, `firstname` and non-null `lastname`/`phone`.
Patch sends only the non-null fields of `EmptyFieldPatch` (fields a
readback confirmed empty). Auth: `Authorization: Bearer <CRM_TOKEN>`
(simulator: `SIM_TOKEN`). Simulator storage is in-memory only; it has
no database and imports no application code.

## Status mapping (adapter, fixed ProviderError vocabulary)

| Wire | Adapter result |
|---|---|
| lookup 404 | `NotFound` |
| create/patch 404 | `not_found` |
| 400 / 422 | `validation_failed` |
| 401 / 403 | `auth_failed` (existing block/resume path) |
| create 409 | read back by email; found -> return contact and link, missing -> `conflict`. Never a second create |
| 429 | `rate_limited`, `Retry-After` (seconds) passed to the scheduler; adapter never sleeps |
| create/patch 5xx, timeout, reset after possible send | `result_unknown` (readback-first; never resend create) |
| lookup/patch-read 5xx or timeout | `unavailable` / `result_unknown` for writes as above |
| connect refused / DNS before send | `connect_failed` |
| malformed JSON / missing `id` or `email` on 2xx | `invalid_response` |
| probe any expected failure | `ProbeResult(ok=False)`, never an exception |

## Timeouts and no-retry policy

Explicit httpx timeouts: connect 3s, read 5s, write 5s, pool 3s, each
capped by the remaining `CallContext.deadline_seconds` (15s) measured
on a monotonic clock from call start — a total deadline, not only a
per-read timeout. The adapter makes exactly one HTTP request per port
call (plus the single read on a definite create-409) and uses no
retrying transport. The durable job owns retries and budgets.
Simulator counters (`GET /__test__/counts`) prove one write per attempt
in `test_crm_http.py`.

## Simulator

```sh
SIM_TOKEN=sim-token PORT=8091 python provider-sim/app.py   # stdlib, local only
```

Fault injection is local-test only (`POST /__test__/fault`, modes:
`error_before_write`, `error_after_commit`, `timeout_after_commit`,
`disconnect_after_commit`, `slow` via `SIM_SLOW_SECONDS`,
`rate_limited`, `malformed`; `POST /__test__/reset`). Never expose the
simulator or its `__test__` surface publicly. Logs print no token,
email, phone or request bodies (the simulator suppresses request logs;
the adapter logs operation/job ids, method, status and timing only).

## Startup (mock / http profiles)

```sh
cp .env.example .env   # CRM_MODE=mock uses http://127.0.0.1:8091 + sim-token defaults
# or CRM_MODE=http CRM_BASE_URL=... CRM_TOKEN=... (missing config fails startup)
docker compose up -d db
cd backend && .venv\Scripts\python.exe -m alembic upgrade head   # with DATABASE_URL set
```

Reproducible end-to-end proof (starts the simulator on a real socket,
submits an enquiry, drains the worker over real HTTP, asserts the
detail is ready and exactly one create was sent):

```sh
cd backend
.venv\Scripts\python.exe -m pytest tests/integration/test_crm_http.py -q
```

## Known gaps

- No persistent worker loop, heartbeat, seed data or Compose service
  for the simulator/worker (PR 3).
- `begin_sync`'s lookup and `resume_provider`'s probe still run inside
  a DB transaction; see `docs/contract-deviations.md`. The adapter's
  total deadline bounds the network wait inside that transaction.
- Simulator is in-memory and single-process; contacts vanish on restart.
