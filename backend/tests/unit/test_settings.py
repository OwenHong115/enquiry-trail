"""R7a red tests: Settings must fail fast on an empty secret.

An empty SESSION_SECRET makes the CSRF token
HMAC-SHA256("", raw_token), which anyone can compute, so a
Settings with no real secret must not construct at all. The
exception asserted throughout is ValueError, the convention for
an invalid value at construction.
"""

from __future__ import annotations

import pytest

from app.settings import Settings

DATABASE_URL = "postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops"


def test_empty_session_secret_raises() -> None:
    with pytest.raises(ValueError):
        Settings(database_url=DATABASE_URL, session_secret="")


def test_whitespace_only_session_secret_raises() -> None:
    with pytest.raises(ValueError):
        Settings(database_url=DATABASE_URL, session_secret="   ")


def test_from_env_without_session_secret_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_from_env_with_empty_session_secret_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("SESSION_SECRET", "")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_valid_session_secret_round_trips() -> None:
    settings = Settings(
        database_url=DATABASE_URL, session_secret="test-session-secret"
    )
    assert settings.session_secret == "test-session-secret"
    assert settings.database_url == DATABASE_URL
