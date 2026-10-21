# LeadOps Kit — Demo 1

Local single-operator demo of a lead intake pipeline for a fictional
renovation company. Python backend under `backend/`.

## Layout

- `backend/app/domain/` — pure normalization, routing and projection logic.
- `backend/app/services/`, `db/`, `api/`, `worker/`, `adapters/`, `demo/`,
  `security/` — layered packages; dependency direction is
  api -> services -> (domain | ports | db).
- `backend/app/bootstrap.py` — single composition root.
- `backend/tests/unit/` — unit suite (no database).

## Commands

Run from `backend/`:

- Unit tests: `uv run pytest tests/unit -q`

## Working rules

- TDD is mandatory: Red -> Green -> Refactor, one vertical slice at a time.
  Write the failing test first and confirm it fails because behaviour is
  missing.
- Assert public outputs and persisted facts only; never private call
  counts or SQL text.
- Dependencies live in `backend/.venv` managed by uv. Never install
  project dependencies into the base interpreter.
- `.env` is generated from `.env.example` and is never committed.
  `uv.lock` and `backend/pyproject.toml` are tracked.
