"""CRM contact sync: create or preserve-and-fill, exactly once."""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy.orm import Session

from app.db import crm_repo
from app.ports.clock import BusinessClock
from app.ports.crm import (
    CallContext,
    CreateContact,
    CrmPort,
    EmptyFieldPatch,
    FoundContact,
    NotFound,
    ProviderError,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.worker.claiming import ClaimedJob

# behavior.yaml worker.http_deadline_seconds.
HTTP_DEADLINE_SECONDS = 15


class WriteResultUnknown(Exception):
    """The provider write may have landed; its result is unknown.

    Carries the external operation id so the worker can move the job
    to reconciling instead of retrying the write blindly.
    """

    def __init__(self, operation_id: uuid.UUID) -> None:
        super().__init__(f"write result unknown for operation {operation_id}")
        self.operation_id = operation_id


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


def sync_contact(
    session: Session,
    claim: ClaimedJob,
    crm: CrmPort,
    clock: BusinessClock,
) -> None:
    """Sync the claimed enquiry's contact to the CRM.

    Runs inside the claim transaction: any CRM exception propagates
    and rolls the whole claim back, so no link, event or succeeded
    job can survive a failed sync.
    """
    contact = crm_repo.get_enquiry_contact(session, claim.entity_id)
    if contact is None:
        raise ValueError(f"enquiry not found: {claim.entity_id}")

    context = CallContext(
        scenario_run_id=None,
        job_id=claim.job_id,
        cycle_no=claim.cycle_no,
        attempt_no=claim.attempt_no,
        external_operation_id=None,
        deadline_seconds=HTTP_DEADLINE_SECONDS,
    )

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
        try:
            created = crm.create_contact(
                CreateContact(
                    email=contact.email_norm,
                    firstname=contact.first_name,
                    lastname=contact.last_name,
                    phone=contact.phone_e164,
                ),
                context,
            )
        except ProviderError as err:
            if err.code == "result_unknown":
                crm_repo.set_operation_unknown(session, operation_id)
                raise WriteResultUnknown(operation_id) from err
            raise
        crm_repo.set_operation_succeeded(session, operation_id, created.remote_id)
        remote_id = created.remote_id
    elif isinstance(found, FoundContact):
        remote_id = found.remote_id
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
            local = local_values[prop]
            remote = remote_values[prop]
            if _is_blank(remote) and not _is_blank(local):
                candidates[prop] = local  # type: ignore[assignment]
            elif (
                not _is_blank(remote)
                and not _is_blank(local)
                and remote != local
            ):
                crm_repo.insert_field_preserved_event(
                    session,
                    enquiry_id=claim.entity_id,
                    property_name=prop,
                    local_masked=_mask(local),  # type: ignore[arg-type]
                    remote_masked=_mask(remote),  # type: ignore[arg-type]
                )
        if candidates:
            probe = crm.probe(context)
            if probe.supports_patch:
                crm.patch_contact(
                    found.remote_id,
                    EmptyFieldPatch(**candidates),
                    context,
                )
    else:  # pragma: no cover - port contract violation
        raise ValueError(f"unexpected CRM lookup result: {found!r}")

    crm_repo.upsert_crm_link(
        session,
        contact_id=contact.contact_id,
        remote_id=remote_id,
        synced_contact_version=contact.version,
    )
    crm_repo.insert_synced_event(
        session,
        enquiry_id=claim.entity_id,
        remote_id=remote_id,
        contact_version=contact.version,
    )


def reconcile_unknown(
    session: Session,
    claim: ClaimedJob,
    crm: CrmPort,
    clock: BusinessClock,
) -> bool:
    """Read back an unknown create by email. True when reconciled.

    Never creates: a NotFound readback only stamps the operation so
    the handler can reschedule the next readback.
    """
    contact = crm_repo.get_enquiry_contact(session, claim.entity_id)
    if contact is None:
        raise ValueError(f"enquiry not found: {claim.entity_id}")
    operation = crm_repo.get_unknown_operation_by_job(session, claim.job_id)
    if operation is None:
        raise ValueError(f"no unknown operation for job {claim.job_id}")

    context = CallContext(
        scenario_run_id=None,
        job_id=claim.job_id,
        cycle_no=claim.cycle_no,
        attempt_no=claim.attempt_no,
        external_operation_id=operation.id,
        deadline_seconds=HTTP_DEADLINE_SECONDS,
    )
    found = crm.get_contact_by_email(contact.email_norm, context)
    if isinstance(found, NotFound):
        crm_repo.touch_operation_readback(session, operation.id)
        return False
    if not isinstance(found, FoundContact):  # pragma: no cover
        raise ValueError(f"unexpected CRM lookup result: {found!r}")

    crm_repo.upsert_crm_link(
        session,
        contact_id=contact.contact_id,
        remote_id=found.remote_id,
        synced_contact_version=contact.version,
    )
    crm_repo.set_operation_reconciled(session, operation.id, found.remote_id)
    crm_repo.insert_recovered_event(
        session,
        enquiry_id=claim.entity_id,
        operation_id=operation.id,
        remote_id=found.remote_id,
    )
    crm_repo.insert_synced_event(
        session,
        enquiry_id=claim.entity_id,
        remote_id=found.remote_id,
        contact_version=contact.version,
    )
    return True
