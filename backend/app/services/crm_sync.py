"""CRM contact sync: create or preserve-and-fill, exactly once."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db import crm_repo
from app.ports.crm import (
    CallContext,
    CreateContact,
    CrmPort,
    EmptyFieldPatch,
    FoundContact,
    NotFound,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.worker.claiming import ClaimedJob

# behavior.yaml worker.http_deadline_seconds.
HTTP_DEADLINE_SECONDS = 15


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _request_hash(fields: dict[str, str]) -> str:
    canonical = json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mask(value: str) -> str:
    """Mask a value for event details: first character plus '***'.

    The contracts do not define a mask format; this one never equals
    the raw value (even a one-character value gains the suffix) and
    is never empty.
    """
    return value[0] + "***"


@dataclass(frozen=True)
class SyncLookup:
    contact: Any
    context: CallContext
    found: FoundContact | NotFound
    operation_id: uuid.UUID | None


def load_contact(session: Session, claim: ClaimedJob) -> Any:
    contact = crm_repo.get_enquiry_contact(session, claim.entity_id)
    if contact is None:
        raise ValueError(f"enquiry not found: {claim.entity_id}")
    return contact


def make_context(
    claim: ClaimedJob, operation_id: uuid.UUID | None = None
) -> CallContext:
    return CallContext(
        scenario_run_id=None,
        job_id=claim.job_id,
        cycle_no=claim.cycle_no,
        attempt_no=claim.attempt_no,
        external_operation_id=operation_id,
        deadline_seconds=HTTP_DEADLINE_SECONDS,
    )


def begin_sync(session: Session, claim: ClaimedJob, crm: CrmPort) -> SyncLookup:
    """Load the contact and do the provider lookup (block 1).

    The intent is inserted only after the lookup returned NotFound,
    in the caller's transaction, so that transaction's commit makes
    the intent durable before any create is dispatched. A lookup
    failure writes no operation row.
    """
    contact = load_contact(session, claim)
    context = make_context(claim)
    found = crm.get_contact_by_email(contact.email_norm, context)
    if isinstance(found, NotFound):
        sent_fields: dict[str, str] = {
            "email": contact.email_norm,
            "firstname": contact.first_name,
        }
        if contact.last_name is not None:
            sent_fields["lastname"] = contact.last_name
        if contact.phone_e164 is not None:
            sent_fields["phone"] = contact.phone_e164
        operation_id = crm_repo.insert_operation_intent(
            session,
            job_id=claim.job_id,
            operation_key=(
                f"hubspot:create:{contact.contact_id}:v{contact.version}"
            ),
            request_hash=_request_hash(sent_fields),
        )
        return SyncLookup(contact, context, found, operation_id)
    if isinstance(found, FoundContact):
        return SyncLookup(contact, context, found, None)
    raise ValueError(f"unexpected CRM lookup result: {found!r}")


def existing_patch_candidates(contact: Any, found: FoundContact) -> dict[str, str]:
    local_values = {
        "firstname": contact.first_name,
        "lastname": contact.last_name,
        "phone": contact.phone_e164,
    }
    remote_values = {
        "firstname": found.firstname,
        "lastname": found.lastname,
        "phone": found.phone,
    }
    candidates: dict[str, str] = {}
    for prop in ("firstname", "lastname", "phone"):
        if _is_blank(remote_values[prop]) and not _is_blank(local_values[prop]):
            candidates[prop] = local_values[prop]  # type: ignore[assignment]
    return candidates


def perform_existing_patch(
    crm: CrmPort,
    context: CallContext,
    found: FoundContact,
    candidates: dict[str, str],
) -> None:
    """Probe and patch empty remote fields (outside a transaction)."""
    if not candidates:
        return
    probe = crm.probe(context)
    if probe.supports_patch:
        crm.patch_contact(found.remote_id, EmptyFieldPatch(**candidates), context)


def record_existing_sync(
    session: Session, claim: ClaimedJob, contact: Any, found: FoundContact
) -> None:
    local_values = {
        "firstname": contact.first_name,
        "lastname": contact.last_name,
        "phone": contact.phone_e164,
    }
    remote_values = {
        "firstname": found.firstname,
        "lastname": found.lastname,
        "phone": found.phone,
    }
    for prop in ("firstname", "lastname", "phone"):
        local = local_values[prop]
        remote = remote_values[prop]
        if not _is_blank(remote) and not _is_blank(local) and remote != local:
            crm_repo.insert_field_preserved_event(
                session,
                enquiry_id=claim.entity_id,
                property_name=prop,
                local_masked=_mask(local),  # type: ignore[arg-type]
                remote_masked=_mask(remote),  # type: ignore[arg-type]
            )
    crm_repo.upsert_crm_link(
        session,
        contact_id=contact.contact_id,
        remote_id=found.remote_id,
        synced_contact_version=contact.version,
    )
    crm_repo.insert_synced_event(
        session,
        enquiry_id=claim.entity_id,
        remote_id=found.remote_id,
        contact_version=contact.version,
    )


def create_remote_contact(
    crm: CrmPort, contact: Any, context: CallContext
) -> FoundContact:
    """Dispatch the create (step 2: no open transaction)."""
    return crm.create_contact(
        CreateContact(
            email=contact.email_norm,
            firstname=contact.first_name,
            lastname=contact.last_name,
            phone=contact.phone_e164,
        ),
        context,
    )


def record_create_success(
    session: Session,
    claim: ClaimedJob,
    contact: Any,
    operation_id: uuid.UUID,
    created: FoundContact,
) -> None:
    crm_repo.set_operation_succeeded(session, operation_id, created.remote_id)
    crm_repo.upsert_crm_link(
        session,
        contact_id=contact.contact_id,
        remote_id=created.remote_id,
        synced_contact_version=contact.version,
    )
    crm_repo.insert_synced_event(
        session,
        enquiry_id=claim.entity_id,
        remote_id=created.remote_id,
        contact_version=contact.version,
    )


def record_readback_found(
    session: Session,
    claim: ClaimedJob,
    contact: Any,
    operation_id: uuid.UUID,
    found: FoundContact,
) -> None:
    crm_repo.upsert_crm_link(
        session,
        contact_id=contact.contact_id,
        remote_id=found.remote_id,
        synced_contact_version=contact.version,
    )
    crm_repo.set_operation_reconciled(session, operation_id, found.remote_id)
    crm_repo.insert_recovered_event(
        session,
        enquiry_id=claim.entity_id,
        operation_id=operation_id,
        remote_id=found.remote_id,
    )
    crm_repo.insert_synced_event(
        session,
        enquiry_id=claim.entity_id,
        remote_id=found.remote_id,
        contact_version=contact.version,
    )
