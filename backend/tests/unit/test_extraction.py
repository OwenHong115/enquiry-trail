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


def test_fixture_differs_from_template() -> None:
    fixture = FixtureExtractor().extract(_req())
    template = TemplateExtractor().extract(_req())
    assert fixture.timeframe == template.timeframe
    assert fixture.timeframe == "next month"
    assert fixture.evidence == template.evidence
    cleaned = validate_extraction(fixture, MSG)
    assert cleaned["timeframe"] == fixture.timeframe
    assert cleaned["budget"] == 20000


def test_runner_profile_snapshot_records_parameters() -> None:
    from app.settings import Settings as _Settings
    from app.worker.runner import _extraction_profile
    profile = _extraction_profile(_Settings(database_url=DB, session_secret="s", extractor_mode="fixture"))
    assert profile.parameters == {"extractor_mode": "fixture", "provider": "fixture"}
    assert profile.parameters != {}


def test_fixture_parses_k_shorthand_budget_template_misreads() -> None:
    # Fixture-only rule: a "20k" budget shorthand means 20000. The
    # template regex has no such rule and reads the bare digits (20),
    # so this case separates the two strategies by extracted value.
    message = "My budget is around 20k. I need a bathroom renovation next month."
    fixture = validate_extraction(FixtureExtractor().extract(_req(message)), message)
    template = validate_extraction(TemplateExtractor().extract(_req(message)), message)
    assert fixture["budget"] == 20000
    assert template["budget"] != 20000


def test_fixture_describes_its_own_strategy_for_snapshot() -> None:
    # The persisted parameters_snapshot must name the fixture strategy
    # and the rules it applied, and must differ from the template's.
    extractor = FixtureExtractor()
    extractor.extract(_req())
    described = extractor.describe_parameters()
    assert described["strategy"] == "fixture-rules"
    assert described["rules"], "fixture must report the rules it applied"
    template_describe = getattr(TemplateExtractor(), "describe_parameters", None)
    template_params = template_describe() if template_describe else {}
    assert described != template_params
    assert template_params.get("strategy") != "fixture-rules"
