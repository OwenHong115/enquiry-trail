"""Template drafts: deterministic template, read and save.

A draft carries its own draft_version, independent of the enquiry
version: saving a draft never bumps enquiries.version, and changing
the owner never bumps draft_version, so the two writes cannot
conflict with each other. Every save appends one draft_edits history
row and one draft.saved event (row locks first, event lock last).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.db import draft_repo, enquiry_repo
from app.ports.clock import BusinessClock
from app.services.errors import EnquiryNotFound, VersionConflict

__all__ = [
    "DraftView",
    "EnquiryNotFound",
    "VersionConflict",
    "build_template",
    "get_draft",
    "save_draft",
]


@dataclass(frozen=True)
class DraftView:
    """A draft as published: saved (version >= 1) or template (0)."""

    enquiry_id: uuid.UUID
    subject: str
    body: str
    draft_version: int
    updated_by: uuid.UUID | None
    updated_at: datetime | None


def build_template(*, first_name: str, service_type: str) -> tuple[str, str]:
    """Deterministic English template draft for one enquiry.

    Pure function of the contact first name and the service type:
    the same inputs always produce the same subject and body, with
    no clock, randomness or external data involved.
    """
    subject = f"Following up on your {service_type} enquiry"
    body = (
        f"Hi {first_name},\n"
        f"\n"
        f"Thank you for getting in touch about your {service_type} project.\n"
        f"\n"
        f"We have received your enquiry and a member of our team will "
        f"review the details you shared. If we need anything else before "
        f"we can prepare next steps, we will let you know.\n"
        f"\n"
        f"If anything changes in the meantime, just reply to this "
        f"message and we will update your enquiry.\n"
        f"\n"
        f"Kind regards,\n"
        f"The EnquiryTrail team"
    )
    return subject, body


def _template_view(session: Session, enquiry_id: uuid.UUID) -> DraftView:
    detail = enquiry_repo.get_enquiry_detail(session, enquiry_id)
    if detail is None:
        raise EnquiryNotFound(f"enquiry not found: {enquiry_id}")
    subject, body = build_template(
        first_name=detail.first_name, service_type=detail.service_type
    )
    return DraftView(
        enquiry_id=enquiry_id,
        subject=subject,
        body=body,
        draft_version=0,
        updated_by=None,
        updated_at=None,
    )


def get_draft(session: Session, enquiry_id: uuid.UUID) -> DraftView:
    """The saved draft, or the unsaved template (draft_version 0)."""
    row = draft_repo.get_draft(session, enquiry_id)
    if row is None:
        return _template_view(session, enquiry_id)
    return DraftView(
        enquiry_id=row.enquiry_id,
        subject=row.subject,
        body=row.body,
        draft_version=row.draft_version,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )


def save_draft(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    subject: str,
    body: str,
    expected_draft_version: int | None,
    operator_id: uuid.UUID,
    clock: BusinessClock,
) -> DraftView:
    """Save a draft under its independent draft_version.

    expected_draft_version=None means "no expectation" for the first
    save only in the sense that an absent draft is version 0; when a
    draft exists, None skips the check and the conditional update
    still serialises on the locked draft row. A provided expectation
    that does not match the current draft version conflicts and
    writes nothing. The enquiry row is locked only to prove the
    enquiry exists; its version is never read or changed here.
    """
    saved_at = clock.now_utc()
    with session.begin():
        enquiry = enquiry_repo.get_enquiry_for_update(session, enquiry_id)
        if enquiry is None:
            raise EnquiryNotFound(f"enquiry not found: {enquiry_id}")

        existing = draft_repo.get_draft_for_update(session, enquiry_id)
        if existing is None:
            if expected_draft_version not in (None, 0):
                raise VersionConflict(
                    f"enquiry {enquiry_id} has no draft, "
                    f"not version {expected_draft_version}"
                )
            new_version = draft_repo.insert_draft(
                session,
                enquiry_id=enquiry_id,
                subject=subject,
                body=body,
                operator_id=operator_id,
                updated_at=saved_at,
            )
        else:
            if (
                expected_draft_version is not None
                and expected_draft_version != existing.draft_version
            ):
                raise VersionConflict(
                    f"draft for enquiry {enquiry_id} is at version "
                    f"{existing.draft_version}, not {expected_draft_version}"
                )
            updated = draft_repo.update_draft(
                session,
                enquiry_id=enquiry_id,
                expected_draft_version=existing.draft_version,
                subject=subject,
                body=body,
                operator_id=operator_id,
                updated_at=saved_at,
            )
            if updated == 0:
                raise VersionConflict(
                    f"draft for enquiry {enquiry_id} changed under the save"
                )
            new_version = existing.draft_version + 1

        draft_repo.insert_draft_edit(
            session,
            enquiry_id=enquiry_id,
            draft_version=new_version,
            subject=subject,
            body=body,
            operator_id=operator_id,
            created_at=saved_at,
        )
        draft_repo.insert_draft_saved_event(
            session,
            enquiry_id=enquiry_id,
            operator_id=operator_id,
            draft_version=new_version,
        )

    return DraftView(
        enquiry_id=enquiry_id,
        subject=subject,
        body=body,
        draft_version=new_version,
        updated_by=operator_id,
        updated_at=saved_at,
    )
