"""Extraction use case: validate, persist, and fall back deterministically.

Extraction only reads the enquiry message. It never routes, never
changes the owner, never creates tasks and has no tool side effects.
Invalid fields are dropped to null; any extractor failure degrades
to the deterministic template fallback while the enquiry prepares.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.extraction_template import TemplateExtractor
from app.db import enrichment_repo
from app.ports.extraction import (
    ExtractionError,
    ExtractionRequest,
    ExtractionResult,
    ExtractorPort,
)

_SUCCESS_STATUS = {"fixture": "fixture", "template": "template_fallback",
                   "connected": "extracted"}


@dataclass(frozen=True)
class ExtractionProfile:
    """Resolved extractor settings recorded in every snapshot."""

    mode: str = "fixture"
    provider: str = "fixture"
    model: str = "fixture-model"
    prompt_version: str = "v1"
    parameters: dict = field(default_factory=dict)


def _clean_budget(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
    elif isinstance(value, str):
        text = re.sub(r"(?i)aud|\$|,|\s", "", value)
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None
    if not number.is_finite() or number < 0:
        return None
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def validate_extraction(result: ExtractionResult, message: str) -> dict:
    """Drop every untrusted field that fails validation to null."""
    budget = _clean_budget(getattr(result, "budget", None))
    timeframe_raw = getattr(result, "timeframe", None)
    timeframe = (
        timeframe_raw.strip()
        if isinstance(timeframe_raw, str) and timeframe_raw.strip()
        else None
    )
    if timeframe is not None and len(timeframe) > 200:
        timeframe = None
    evidence_raw = getattr(result, "evidence", None)
    evidence = (
        evidence_raw
        if isinstance(evidence_raw, str)
        and evidence_raw.strip()
        and evidence_raw in message
        else None
    )
    return {"budget": budget, "timeframe": timeframe, "evidence": evidence}


def extract_and_persist(
    session: Session,
    enquiry_id: uuid.UUID,
    extractor: ExtractorPort,
    profile: ExtractionProfile,
) -> Any | None:
    """Extract for one enquiry and persist its single enrichment row.

    Idempotent: an existing row is returned untouched, so a lease
    recovery re-run neither duplicates nor crashes. Extractor
    failures persist a template_fallback row instead of raising.
    """
    existing = enrichment_repo.get_enrichment(session, enquiry_id)
    if existing is not None:
        return existing
    message = enrichment_repo.get_enquiry_message(session, enquiry_id)
    if message is None:
        return None
    snapshot = {
        "model": profile.model,
        "prompt_version": profile.prompt_version,
        "parameters": dict(profile.parameters),
    }
    status = _SUCCESS_STATUS.get(profile.mode, "extracted")
    last_error_code: str | None = None
    try:
        raw = extractor.extract(
            ExtractionRequest(enquiry_id=enquiry_id, message=message)
        )
        if not isinstance(raw, ExtractionResult):
            raise ExtractionError("invalid_response")
        cleaned = validate_extraction(raw, message)
        describe = getattr(extractor, "describe_parameters", None)
        if callable(describe):
            snapshot["parameters"] = {**snapshot["parameters"], **describe()}
    except ExtractionError as err:
        last_error_code = err.code
        status = "template_fallback"
        cleaned = validate_extraction(
            TemplateExtractor().extract(
                ExtractionRequest(enquiry_id=enquiry_id, message=message)
            ),
            message,
        )
    except Exception:
        last_error_code = "unavailable"
        status = "template_fallback"
        cleaned = validate_extraction(
            TemplateExtractor().extract(
                ExtractionRequest(enquiry_id=enquiry_id, message=message)
            ),
            message,
        )
    row = enrichment_repo.insert_enrichment(
        session,
        enquiry_id=enquiry_id,
        status=status,
        input_hash=enrichment_repo.message_input_hash(message),
        provider=profile.provider,
        model=profile.model,
        prompt_version=profile.prompt_version,
        parameters_snapshot=snapshot,
        result_json=cleaned,
        last_error_code=last_error_code,
    )
    enrichment_repo.insert_enrichment_event(
        session,
        enquiry_id=enquiry_id,
        event_type=(
            "enrichment.fallback"
            if status == "template_fallback"
            else "enrichment.completed"
        ),
        status=status,
    )
    return row
