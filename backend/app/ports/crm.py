"""CrmPort: the CRM boundary interface (no HTTP in this layer)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CallContext:
    """Per-call context carried to every provider operation."""

    scenario_run_id: uuid.UUID | None
    job_id: uuid.UUID
    cycle_no: int
    attempt_no: int
    external_operation_id: uuid.UUID | None
    deadline_seconds: float


@dataclass(frozen=True)
class FoundContact:
    remote_id: str
    email: str
    firstname: str | None
    lastname: str | None
    phone: str | None


@dataclass(frozen=True)
class NotFound:
    """An explicit provider not-found, distinct from a failure."""

    email: str | None = None
    remote_id: str | None = None


@dataclass(frozen=True)
class CreateContact:
    email: str
    firstname: str
    lastname: str | None = None
    phone: str | None = None


@dataclass(frozen=True)
class EmptyFieldPatch:
    """Only fields a readback confirmed empty may be set here."""

    firstname: str | None = None
    lastname: str | None = None
    phone: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    supports_patch: bool


_PROVIDER_ERROR_CODES = frozenset(
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


# Public name for the same vocabulary ProviderError validates
# against, for callers that must record an allowed code.
PROVIDER_ERROR_CODES = _PROVIDER_ERROR_CODES


class ProviderError(Exception):
    """A classified provider failure (vocabulary ProviderError codes)."""

    def __init__(
        self,
        code: str,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        if code not in _PROVIDER_ERROR_CODES:
            raise ValueError(f"unknown provider error code: {code!r}")
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


class CrmPort(Protocol):
    """The swappable CRM interface the sync service depends on."""

    def get_contact_by_email(
        self, email_norm: str, context: CallContext
    ) -> FoundContact | NotFound: ...

    def get_contact_by_id(
        self, remote_id: str, context: CallContext
    ) -> FoundContact | NotFound: ...

    def create_contact(
        self, fields: CreateContact, context: CallContext
    ) -> FoundContact: ...

    def patch_contact(
        self, remote_id: str, fields: EmptyFieldPatch, context: CallContext
    ) -> FoundContact: ...

    def probe(self, context: CallContext) -> ProbeResult: ...
