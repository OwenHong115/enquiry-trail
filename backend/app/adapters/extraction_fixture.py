"""FixtureExtractor: canned deterministic extraction for tests/demos."""

from __future__ import annotations

from app.adapters.extraction_template import TemplateExtractor
from app.ports.extraction import ExtractionRequest, ExtractionResult


class FixtureExtractor:
    """Returns canned extraction derived deterministically from the message."""

    def __init__(self) -> None:
        self._template = TemplateExtractor()

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        return self._template.extract(request)
