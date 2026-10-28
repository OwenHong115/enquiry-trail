"""TemplateExtractor: deterministic no-AI fallback extraction."""

from __future__ import annotations

import re

from app.ports.extraction import ExtractionRequest, ExtractionResult

_BUDGET_RE = re.compile(
    r"(?:AUD\s*\$?|\$)\s*([\d,]+(?:\.\d+)?)|budget[^.\d]*?([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
_TIMEFRAME_RE = re.compile(
    r"(next\s+(?:week|month|year)|within\s+\d+\s+(?:days?|weeks?|months?)"
    r"|in\s+\d+\s+(?:days?|weeks?|months?)|asap|immediately)",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"[^.]+\.")


def _sentence_containing(message: str, start: int) -> str | None:
    for match in _SENTENCE_RE.finditer(message):
        if match.start() <= start < match.end():
            return match.group(0).strip()
    return None


class TemplateExtractor:
    """Regex-only extraction; never calls a model, never has side effects."""

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        message = request.message
        budget: str | None = None
        evidence: str | None = None
        budget_match = _BUDGET_RE.search(message)
        if budget_match is not None:
            budget = budget_match.group(1) or budget_match.group(2)
            evidence = _sentence_containing(message, budget_match.start())
        timeframe: str | None = None
        time_match = _TIMEFRAME_RE.search(message)
        if time_match is not None:
            timeframe = time_match.group(1).strip()
            if evidence is None:
                evidence = _sentence_containing(message, time_match.start())
        return ExtractionResult(
            budget=budget, timeframe=timeframe, evidence=evidence
        )
