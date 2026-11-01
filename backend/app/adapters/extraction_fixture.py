"""FixtureExtractor: rule-based deterministic extraction for tests/demos.

This is a genuinely independent strategy from TemplateExtractor: it
parses the message with its own ordered keyword rules (including a
"k" budget shorthand the template regex does not know), returns the
verbatim timeframe phrase, and reports the rules it applied so the
persisted parameters_snapshot can name strategy=fixture-rules.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.ports.extraction import ExtractionRequest, ExtractionResult

# Ordered (rule name, pattern) pairs; the first budget and the first
# timeframe rule that match win, so output is deterministic.
_BUDGET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("budget:k-shorthand", re.compile(r"(\d+(?:\.\d+)?)\s*k\b", re.IGNORECASE)),
    ("budget:aud-amount", re.compile(r"AUD\s*\$?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)),
    ("budget:dollar-amount", re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")),
    ("budget:keyword-amount", re.compile(r"budget[^.\d]*?([\d,]+(?:\.\d+)?)", re.IGNORECASE)),
)
_TIMEFRAME_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("timeframe:next-period", re.compile(r"next\s+(?:week|month|year)", re.IGNORECASE)),
    ("timeframe:within-period", re.compile(r"within\s+\d+\s+(?:days?|weeks?|months?)", re.IGNORECASE)),
    ("timeframe:in-period", re.compile(r"in\s+\d+\s+(?:days?|weeks?|months?)", re.IGNORECASE)),
    ("timeframe:urgent", re.compile(r"asap|immediately", re.IGNORECASE)),
)
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?")

STRATEGY = "fixture-rules"
ALL_RULES: tuple[str, ...] = tuple(
    name for name, _ in (*_BUDGET_RULES, *_TIMEFRAME_RULES)
)


def _sentence_containing(message: str, start: int) -> str | None:
    for match in _SENTENCE_RE.finditer(message):
        if match.start() <= start < match.end():
            return match.group(0).strip() or None
    return None


class FixtureExtractor:
    """Deterministic rule-based extraction; never calls a model."""

    strategy = STRATEGY

    def __init__(self) -> None:
        self._applied_rules: list[str] = []

    def describe_parameters(self) -> dict:
        """Snapshot fragment: strategy name plus rules last applied."""
        return {"strategy": STRATEGY, "rules": list(self._applied_rules)}

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        message = request.message
        applied: list[str] = []
        budget: object = None
        evidence: str | None = None
        for name, pattern in _BUDGET_RULES:
            match = pattern.search(message)
            if match is None:
                continue
            applied.append(name)
            if name == "budget:k-shorthand":
                budget = Decimal(match.group(1)) * 1000
                budget = int(budget) if budget == budget.to_integral_value() else float(budget)
            else:
                budget = match.group(1)
            evidence = _sentence_containing(message, match.start())
            break
        timeframe: str | None = None
        for name, pattern in _TIMEFRAME_RULES:
            match = pattern.search(message)
            if match is None:
                continue
            applied.append(name)
            timeframe = match.group(0).strip()
            if evidence is None:
                evidence = _sentence_containing(message, match.start())
            break
        self._applied_rules = applied
        return ExtractionResult(budget=budget, timeframe=timeframe, evidence=evidence)
