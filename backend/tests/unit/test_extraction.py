"""Extraction port, validation, adapters and profile settings."""

from __future__ import annotations

import pytest

from app.adapters.extraction_fixture import FixtureExtractor
from app.adapters.extraction_template import TemplateExtractor
from app.ports.extraction import (
    ExtractionError,
    ExtractionRequest,
    ExtractionResult,
)
from app.services.extraction import validate_extraction
from app.settings import Settings

DB = "postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops"
MSG = "I need a bathroom renovation next month. My budget is around AUD 20,000."


def _req(message: str = MSG) -> ExtractionRequest:
    import uuid

    return ExtractionRequest(enquiry_id=uuid.uuid4(), message=message)


def test_validate_keeps_valid_fields() -> None:
    cleaned = validate_extraction(
        ExtractionResult(
            budget=20000,
            timeframe="next month",
            evidence="My budget is around AUD 20,000.",
        ),
        MSG,
    )
    assert cleaned == {
        "budget": 20000,
        "timeframe": "next month",
        "evidence": "My budget is around AUD 20,000.",
    }


def test_validate_drops_non_numeric_budget() -> None:
    cleaned = validate_extraction(
        ExtractionResult(budget="lots", timeframe="soon", evidence=None), MSG
    )
    assert cleaned["budget"] is None


def test_validate_drops_negative_budget() -> None:
    cleaned = validate_extraction(
        ExtractionResult(budget=-5, timeframe=None, evidence=None), MSG
    )
    assert cleaned["budget"] is None


def test_validate_drops_bool_budget() -> None:
    cleaned = validate_extraction(
        ExtractionResult(budget=True, timeframe=None, evidence=None), MSG
    )
    assert cleaned["budget"] is None


def test_validate_parses_currency_string_budget() -> None:
    cleaned = validate_extraction(
        ExtractionResult(budget="AUD 20,000", timeframe=None, evidence=None), MSG
    )
    assert cleaned["budget"] == 20000


def test_validate_drops_evidence_not_in_message() -> None:
    cleaned = validate_extraction(
        ExtractionResult(
            budget=None, timeframe=None, evidence="The budget is 999999 dollars."
        ),
        MSG,
    )
    assert cleaned["evidence"] is None


def test_validate_drops_blank_timeframe() -> None:
    cleaned = validate_extraction(
        ExtractionResult(budget=None, timeframe="   ", evidence=None), MSG
    )
    assert cleaned["timeframe"] is None


def test_extraction_error_rejects_unknown_code() -> None:
    with pytest.raises(ValueError):
        ExtractionError("not_a_real_code")
    err = ExtractionError("unavailable")
    assert err.code == "unavailable"


def test_fixture_extractor_extracts_from_message() -> None:
    result = FixtureExtractor().extract(_req())
    cleaned = validate_extraction(result, MSG)
    assert cleaned["budget"] == 20000
    assert cleaned["evidence"] is not None
    assert cleaned["evidence"] in MSG


def test_template_extractor_is_deterministic() -> None:
    extractor = TemplateExtractor()
    first = extractor.extract(_req())
    second = extractor.extract(_req())
    assert first == second
    cleaned = validate_extraction(first, MSG)
    assert cleaned["budget"] == 20000


def test_template_extractor_no_match_returns_nulls() -> None:
    result = TemplateExtractor().extract(_req("Please call me about a kitchen."))
    cleaned = validate_extraction(result, "Please call me about a kitchen.")
    assert cleaned == {"budget": None, "timeframe": None, "evidence": None}


def test_illegal_extractor_mode_fails_startup() -> None:
    with pytest.raises(ValueError):
        Settings(database_url=DB, session_secret="s", extractor_mode="bogus")


def test_connected_mode_without_model_fails_startup() -> None:
    with pytest.raises(ValueError):
        Settings(
            database_url=DB,
            session_secret="s",
            extractor_mode="connected",
            extractor_model="",
        )


def test_connected_mode_with_model_is_allowed() -> None:
    settings = Settings(
        database_url=DB,
        session_secret="s",
        extractor_mode="connected",
        extractor_model="some-model",
    )
    assert settings.extractor_mode == "connected"


def test_blank_prompt_version_fails_startup() -> None:
    with pytest.raises(ValueError):
        Settings(
            database_url=DB,
            session_secret="s",
            extractor_prompt_version="  ",
        )


def test_default_profile_is_fixture() -> None:
    settings = Settings(database_url=DB, session_secret="s")
    assert settings.extractor_mode == "fixture"
    assert settings.extractor_prompt_version
