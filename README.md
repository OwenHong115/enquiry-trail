# EnquiryTrail

EnquiryTrail is a single-operator lead intake pipeline for a fictional
renovation company. An enquiry arrives, is deduplicated, routed to an
owner, prepared in the background, synced to a CRM contact, and tracked
as a local follow-up task. It is a synthetic demo system: do not enter
real customer data.

## Quick start

Prerequisites: Docker Desktop running, Python 3.12, and a `.env` file.

```sh
cp .env.example .env
# Set SESSION_SECRET to a private random value and
# SEED_OPERATOR_PASSWORD to a demo operator password in .env.
docker compose up -d --build
```

The API is at http://127.0.0.1:8080 (health: `/api/v1/health/live`)
and the web UI is at http://127.0.0.1:5173. The `migrate` service runs
`alembic upgrade head` and the seed before `api` and `worker` start;
the API never migrates on start-up.

## Tests

Backend unit tests need no database; integration tests need the
compose database running first, as documented in `AGENTS.md`:

```sh
cd backend
.venv\Scripts\python.exe -m pytest tests/unit -q
cd ..
docker compose up -d db
cd backend
.venv\Scripts\python.exe -m pytest tests/integration -q
```

`uv run pytest ...` is equivalent when `uv` is on PATH. The integration
suite creates its own test database, applies the Alembic migration, and
drops the database afterwards; it never touches the development
database.

Static checks:

```sh
cd backend
uv run ruff check app tests
cd ../frontend
npm run lint
```

Frontend tests and build:

```sh
cd frontend
npm run test
npm run build
```

Browser (e2e) tests, with the compose stack running and the seeded
operator credentials provided:

```sh
cd e2e
npm ci
npx playwright install chromium
E2E_USERNAME=demo-operator E2E_PASSWORD=<seed-operator-password> npm run test
```

On Windows PowerShell, set `E2E_USERNAME` and `E2E_PASSWORD` as
environment variables before running `npm run test`. Without those
credentials the credential-gated specs FAIL (not skip); in CI they are provided
from CI-only values so the suite runs in full.

Live provider tests (opt-in only, against a dedicated test account;
never against the simulator):

```sh
cd backend
python -m pytest tests/live --run-live -q
```

This requires `CRM_MODE=http`, a real `CRM_BASE_URL` and `CRM_TOKEN`,
and `LIVE_TEST_ACCOUNT_MARKER`. Without those prerequisites the suite
fails loudly naming each missing item. Connected mode is unverified in
this delivery; see the handover document below.

## Documentation

- [Connected handover and recovery runbook](docs/connected-handover.md)
- [Acceptance log](docs/acceptance-log.md)
- [Checklist](docs/checklist.md)
- [Agent guide](AGENTS.md)
