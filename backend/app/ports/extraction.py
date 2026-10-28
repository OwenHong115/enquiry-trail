"""ExtractorPort: the extraction boundary interface (no AI in this layer)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

_EXTRACTION_ERROR_CODES = frozenset(
    {
        "connect_failed",
        "rate_limited",
        "unavailable",
        "auth_failed",
        "validation_failed",
        "not_found",
        "conflict",
        "result_unknown",
        "invalid_response",
        "director_unavailable",
    }
)

EXTRACTION_ERROR_CODES = _EXTRACTION_ERROR_CODES


@dataclass(frozen=True)
class ExtractionRequest:
    """One enquiry message to extract budget/timeframe/evidence from."""

    enquiry_id: uuid.UUID
    message: str


@dataclass(frozen=True)
class ExtractionResult:
    """Raw extractor output; fields are untrusted until validated."""

    budget: Any = None
    timeframe: str | None = None
    evidence: str | None = None


class ExtractionError(Exception):
    """A classified extraction failure (provider error vocabulary)."""

    def __init__(self, code: str) -> None:
        if code not in _EXTRACTION_ERROR_CODES:
            raise ValueError(f"unknown extraction error code: {code!r}")
        super().__init__(code)
        self.code = code


class ExtractorPort(Protocol):
    """The swappable extractor interface the extraction service uses."""

    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...
