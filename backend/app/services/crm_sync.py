"""CRM contact sync: create or preserve-and-fill, exactly once."""

from __future__ import annotations

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
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.worker.claiming import ClaimedJob

# behavior.yaml worker.http_deadline_seconds.
HTTP_DEADLINE_SECONDS = 15


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


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
        created = crm.create_contact(
            CreateContact(
                email=contact.email_norm,
                firstname=contact.first_name,
                lastname=contact.last_name,
                phone=contact.phone_e164,
            ),
            context,
        )
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
