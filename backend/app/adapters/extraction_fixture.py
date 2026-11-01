"""FixtureExtractor: canned deterministic extraction for tests/demos."""

from __future__ import annotations

from app.adapters.extraction_template import TemplateExtractor
from app.ports.extraction import ExtractionRequest, ExtractionResult


class FixtureExtractor:
    """Returns canned extraction derived deterministically from the message."""

    def __init__(self) -> None:
        self._template = TemplateExtractor()

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        result = self._template.extract(request)
        # Distinguishable from a bare template alias: fixture mode marks
        # its timeframe deterministically while keeping evidence verbatim
        # so validation still accepts it as a substring of the message.
        timeframe = f"{result.timeframe} (fixture)" if result.timeframe else "fixture"
        return ExtractionResult(budget=result.budget, timeframe=timeframe, evidence=result.evidence)
