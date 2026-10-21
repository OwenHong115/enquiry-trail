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
