"""FakeCrm: scripted CrmPort responses that record every call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ports.crm import (
    CallContext,
    CreateContact,
    EmptyFieldPatch,
    FoundContact,
    NotFound,
    ProbeResult,
)


@dataclass(frozen=True)
class RecordedCall:
    method: str
    fields: dict[str, Any]
    context: CallContext


@dataclass
class FakeCrm:
    """Scripted by constructor; unscripted reads return NotFound."""

    by_email: FoundContact | NotFound | BaseException = field(
        default_factory=NotFound
    )
    by_id: FoundContact | NotFound | BaseException = field(
        default_factory=NotFound
    )
    create_remote_id: str = "hs-created-1"
    patch_supported: bool = True
    probe_ok: bool = True
    error: BaseException | None = None
    calls: list[RecordedCall] = field(default_factory=list)

    def calls_of(self, method: str) -> list[RecordedCall]:
        return [call for call in self.calls if call.method == method]

    def _maybe_raise(self) -> None:
        if self.error is not None:
            raise self.error

    def get_contact_by_email(
        self, email_norm: str, context: CallContext
    ) -> FoundContact | NotFound:
        self.calls.append(
            RecordedCall("get_contact_by_email", {"email": email_norm}, context)
        )
        self._maybe_raise()
        if isinstance(self.by_email, BaseException):
            raise self.by_email
        return self.by_email

    def get_contact_by_id(
        self, remote_id: str, context: CallContext
    ) -> FoundContact | NotFound:
        self.calls.append(
            RecordedCall("get_contact_by_id", {"remote_id": remote_id}, context)
        )
        self._maybe_raise()
        if isinstance(self.by_id, BaseException):
            raise self.by_id
        return self.by_id

    def create_contact(
        self, fields: CreateContact, context: CallContext
    ) -> FoundContact:
        sent: dict[str, Any] = {"email": fields.email, "firstname": fields.firstname}
        if fields.lastname is not None:
            sent["lastname"] = fields.lastname
        if fields.phone is not None:
            sent["phone"] = fields.phone
        self.calls.append(RecordedCall("create_contact", sent, context))
        self._maybe_raise()
        return FoundContact(
            remote_id=self.create_remote_id,
            email=fields.email,
            firstname=fields.firstname,
            lastname=fields.lastname,
            phone=fields.phone,
        )

    def patch_contact(
        self, remote_id: str, fields: EmptyFieldPatch, context: CallContext
    ) -> FoundContact:
        sent: dict[str, Any] = {}
        if fields.firstname is not None:
            sent["firstname"] = fields.firstname
        if fields.lastname is not None:
            sent["lastname"] = fields.lastname
        if fields.phone is not None:
            sent["phone"] = fields.phone
        self.calls.append(
            RecordedCall(
                "patch_contact", {"remote_id": remote_id, **sent}, context
            )
        )
        self._maybe_raise()
        base = self.by_email if isinstance(self.by_email, FoundContact) else None
        return FoundContact(
            remote_id=remote_id,
            email=base.email if base else "",
            firstname=sent.get("firstname", base.firstname if base else None),
            lastname=sent.get("lastname", base.lastname if base else None),
            phone=sent.get("phone", base.phone if base else None),
        )

    def probe(self, context: CallContext) -> ProbeResult:
        self.calls.append(RecordedCall("probe", {}, context))
        self._maybe_raise()
        return ProbeResult(ok=self.probe_ok, supports_patch=self.patch_supported)
