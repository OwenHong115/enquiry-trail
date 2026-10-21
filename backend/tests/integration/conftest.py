"""PostgreSQL harness for integration tests.

One throwaway database per session, migrated with Alembic, dropped at
teardown. There is no SQLite or in-memory fallback: a missing database
or a failed migration is an error, never a skip.
"""

from __future__ import annotations

import os
import secrets
import sys
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[2]

TABLES_UNDER_TEST = (
    "leadops.ingestion_receipts",
    "leadops.follow_up_tasks",
    "leadops.jobs",
    "leadops.enquiries",
    "leadops.contacts",
)


def _dev_database_url() -> URL:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        env_file = BACKEND_DIR.parent / ".env"
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                raw = line.split("=", 1)[1].strip()
                break
    if not raw:
        raise RuntimeError("DATABASE_URL is not set and no .env provided one")
    return make_url(raw)


def _create_database(server_url: URL, name: str) -> None:
    engine = create_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()


def _drop_database(server_url: URL, name: str) -> None:
    # Refuse to drop anything this harness did not create.
    if not name.startswith("leadops_test_"):
        raise RuntimeError(f"refusing to drop database {name!r}")
    engine = create_engine(server_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def postgres_db() -> Iterator[str]:
    """Create, migrate and yield one leadops_test_<8 hex> database URL."""
    dev_url = _dev_database_url()
    name = f"leadops_test_{secrets.token_hex(4)}"
    if name == dev_url.database:
        raise RuntimeError("test database name collides with the dev database")
    server_url = dev_url.set(database="postgres")
    test_url = dev_url.set(database=name)

    _create_database(server_url, name)
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
    try:
        alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        command.upgrade(alembic_cfg, "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous

    try:
        yield test_url.render_as_string(hide_password=False)
    finally:
        _drop_database(server_url, name)


@pytest.fixture(autouse=True)
def _truncate_tables(postgres_db: str) -> Iterator[None]:
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE "
                    + ", ".join(TABLES_UNDER_TEST)
                    + " RESTART IDENTITY CASCADE"
                )
            )
    finally:
        engine.dispose()
    yield


@pytest.fixture()
def db_session(postgres_db: str) -> Iterator[Session]:
    engine = create_engine(postgres_db)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
