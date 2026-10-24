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
    "leadops.sessions",
    "leadops.integration_health",
    "leadops.ingestion_receipts",
    "leadops.activity_events",
    "leadops.follow_up_tasks",
    "leadops.jobs",
    "leadops.enquiries",
    "leadops.contacts",
)

REFERENCE_OWNERS = (
    ("intake", "Intake Desk"),
    ("maya", "Maya Chen"),
    ("oliver", "Oliver Reed"),
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


@pytest.fixture(autouse=True)
def _seed_reference_owners(
    postgres_db: str, _truncate_tables: None
) -> Iterator[None]:
    # Reference data only: the intake service resolves owners by code
    # and must never create them, so the harness seeds them instead.
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            for code, display_name in REFERENCE_OWNERS:
                conn.execute(
                    text(
                        "INSERT INTO leadops.owners (code, display_name) "
                        "VALUES (:code, :display_name) "
                        "ON CONFLICT (code) DO NOTHING"
                    ),
                    {"code": code, "display_name": display_name},
                )
    finally:
        engine.dispose()
    yield


@pytest.fixture()
def seeded_operator(postgres_db: str) -> object:
    """Seed the one reference operator and return its id.

    Operators are reference data like owners: services resolve them,
    they never create them. The password hash is a placeholder; no
    login behaviour exists in these slices.
    """
    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO leadops.operators (username, password_hash) "
                    "VALUES ('demo-operator', 'placeholder-not-a-real-hash') "
                    "ON CONFLICT (username) DO NOTHING"
                )
            )
            operator_id = conn.execute(
                text(
                    "SELECT id FROM leadops.operators "
                    "WHERE username = 'demo-operator'"
                )
            ).scalar_one()
    finally:
        engine.dispose()
    return operator_id


API_OPERATOR_USERNAME = "intake-operator"
API_OPERATOR_PASSWORD = "intake-demo-pass"


@pytest.fixture()
def api_operator(postgres_db: str):
    """Seed the API-suite operator (distinct from the auth suite)."""
    from app.security.passwords import hash_password

    engine = create_engine(postgres_db)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO leadops.operators (username, password_hash) "
                    "VALUES (:username, :password_hash) "
                    "ON CONFLICT (username) DO NOTHING"
                ),
                {
                    "username": API_OPERATOR_USERNAME,
                    "password_hash": hash_password(API_OPERATOR_PASSWORD),
                },
            )
            operator_id = conn.execute(
                text(
                    "SELECT id FROM leadops.operators WHERE username = :username"
                ),
                {"username": API_OPERATOR_USERNAME},
            ).scalar_one()
    finally:
        engine.dispose()
    return operator_id


@pytest.fixture()
def api_client(postgres_db: str, api_operator):
    """A TestClient already logged in; csrf token on .csrf_token."""
    from fastapi.testclient import TestClient

    from app.bootstrap import create_app
    from app.db.session import get_session
    from app.settings import Settings

    settings = Settings(
        database_url=postgres_db,
        app_base_url="http://testserver",
        session_secret="test-session-secret",
    )
    app = create_app(settings)
    factory_engine = create_engine(postgres_db)
    factory = sessionmaker(bind=factory_engine)

    def override_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": API_OPERATOR_USERNAME,
                "password": API_OPERATOR_PASSWORD,
            },
        )
        assert response.status_code == 200, response.text
        client.csrf_token = response.json()["csrf_token"]  # type: ignore[attr-defined]
        try:
            yield client
        finally:
            # Return every pooled connection this fixture opened
            # (the app engine and the session factory's engine),
            # so a completed run leaves no test connections held.
            app.state.engine.dispose()
            factory_engine.dispose()


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
