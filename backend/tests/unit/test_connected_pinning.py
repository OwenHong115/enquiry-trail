"""Connected-profile startup pinning (mock-labelled delivery).

Connected mode is UNVERIFIED. These tests pin the existing startup
validation only: bad connected configuration must fail fast, and
connected extraction must resolve to a loud failure instead of fixture
data. They never call a real provider and never sign off acceptance.
"""

from __future__ import annotations

import pytest

from app.settings import Settings
from app.worker.runner import _resolve_crm, _resolve_extractor

DB = "postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops"
SECRET = "test-session-secret"


def test_http_mode_without_base_url_or_token_fails_startup() -> None:
    with pytest.raises(ValueError, match="CRM_BASE_URL and CRM_TOKEN"):
        Settings(database_url=DB, session_secret=SECRET, crm_mode="http")


def test_http_mode_without_token_fails_startup() -> None:
    with pytest.raises(ValueError, match="CRM_BASE_URL and CRM_TOKEN"):
        Settings(
            database_url=DB,
            session_secret=SECRET,
            crm_mode="http",
            crm_base_url="https://crm.example.invalid",
        )


def test_http_mode_without_base_url_fails_startup() -> None:
    with pytest.raises(ValueError, match="CRM_BASE_URL and CRM_TOKEN"):
        Settings(
            database_url=DB,
            session_secret=SECRET,
            crm_mode="http",
            crm_token="placeholder-token",
        )


def test_http_mode_from_env_without_token_fails_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("CRM_MODE", "http")
    monkeypatch.setenv("CRM_BASE_URL", "https://crm.example.invalid")
    monkeypatch.delenv("CRM_TOKEN", raising=False)
    with pytest.raises(ValueError, match="CRM_BASE_URL and CRM_TOKEN"):
        Settings.from_env()


def test_http_mode_with_complete_config_is_allowed() -> None:
    settings = Settings(
        database_url=DB,
        session_secret=SECRET,
        crm_mode="http",
        crm_base_url="https://crm.example.invalid",
        crm_token="placeholder-token",
    )
    assert settings.crm_mode == "http"
    # Resolution builds the real HTTP adapter; it does not fall back
    # to a fake. No network call is made by construction.
    from app.adapters.crm_http import HttpCrmAdapter

    assert isinstance(_resolve_crm(settings), HttpCrmAdapter)


def test_connected_extraction_without_model_fails_startup() -> None:
    with pytest.raises(ValueError, match="EXTRACTOR_MODEL"):
        Settings(
            database_url=DB,
            session_secret=SECRET,
            extractor_mode="connected",
            extractor_model="",
        )
    with pytest.raises(ValueError, match="EXTRACTOR_MODEL"):
        Settings(
            database_url=DB,
            session_secret=SECRET,
            extractor_mode="connected",
            extractor_model="   ",
        )


def test_connected_extraction_from_env_without_model_fails_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Via the environment, EXTRACTOR_MODEL is unset: connected mode
    # must still fail fast, never inherit the fixture default model.
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("EXTRACTOR_MODE", "connected")
    monkeypatch.delenv("EXTRACTOR_MODEL", raising=False)
    with pytest.raises(ValueError, match="EXTRACTOR_MODEL"):
        Settings.from_env()


def test_connected_extraction_resolves_to_loud_failure_not_fixture() -> None:
    settings = Settings(
        database_url=DB,
        session_secret=SECRET,
        extractor_mode="connected",
        extractor_model="placeholder-model",
    )
    with pytest.raises(RuntimeError, match="connected extraction is gated"):
        _resolve_extractor(settings)


def test_connected_extraction_via_bootstrap_is_loud_failure() -> None:
    # create_app resolves the extractor at startup; connected mode
    # must raise there instead of wiring fixture data into the app.
    from app.bootstrap import create_app

    settings = Settings(
        database_url=DB,
        session_secret=SECRET,
        extractor_mode="connected",
        extractor_model="placeholder-model",
    )
    with pytest.raises(RuntimeError, match="connected extraction is gated"):
        create_app(settings)


def test_fixture_and_template_extraction_still_resolve() -> None:
    from app.adapters.extraction_fixture import FixtureExtractor
    from app.adapters.extraction_template import TemplateExtractor

    fixture = Settings(database_url=DB, session_secret=SECRET)
    assert isinstance(_resolve_extractor(fixture), FixtureExtractor)
    template = Settings(
        database_url=DB, session_secret=SECRET, extractor_mode="template"
    )
    assert isinstance(_resolve_extractor(template), TemplateExtractor)
