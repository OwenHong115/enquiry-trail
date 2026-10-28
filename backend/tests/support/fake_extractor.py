"""FakeExtractor: scripted ExtractorPort for tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ports.extraction import ExtractionRequest, ExtractionResult


@dataclass
class FakeExtractor:
    """Returns a scripted result or raises a scripted error."""

    result: ExtractionResult | None = None
    error: BaseException | None = None
    calls: list[ExtractionRequest] = field(default_factory=list)

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if self.result is not None:
            return self.result
        return ExtractionResult(budget=None, timeframe=None, evidence=None)
