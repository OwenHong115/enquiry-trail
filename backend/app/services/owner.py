"""Owner actions: change an enquiry owner with optimistic locking.

One transaction, in lock order (row locks first, the event advisory
lock last, per app/db/event_repo.py): lock the enquiry row, check the
expected enquiry version, resolve the new owner, update the enquiry,
move the open follow-up task to the same owner, then write exactly
one owner.changed event. A stale version writes nothing.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db import enquiry_repo, task_repo
from app.ports.clock import BusinessClock
from app.services.errors import EnquiryNotFound, VersionConflict

__all__ = [
    "EnquiryNotFound",
    "UnknownOwner",
    "VersionConflict",
    "change_owner",
]


class UnknownOwner(ValueError):
    """No owner exists with the given code (see EnquiryNotFound)."""


def change_owner(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    new_owner_code: str,
    expected_version: int,
    operator_id: uuid.UUID,
    clock: BusinessClock,
) -> None:
    """Change an enquiry owner, keeping the open task consistent.

    Re-assigning the current owner is a no-op: no version bump, no
    task write and no event, however the call was versioned (the
    version check still applies first, so a stale no-op conflicts).
    The clock is accepted for symmetry with the other operator
    actions; an owner change stamps no business-time column.
    """
    _ = clock
    with session.begin():
        enquiry = enquiry_repo.get_enquiry_for_update(session, enquiry_id)
        if enquiry is None:
            raise EnquiryNotFound(f"enquiry not found: {enquiry_id}")
        if enquiry.version != expected_version:
            raise VersionConflict(
                f"enquiry {enquiry_id} is at version {enquiry.version}, "
                f"not {expected_version}"
            )
        new_owner = enquiry_repo.get_owner_by_code(session, new_owner_code)
        if new_owner is None:
            raise UnknownOwner(f"owner not found: {new_owner_code}")
        if enquiry.owner_id == new_owner.id:
            return

        updated = enquiry_repo.update_enquiry_owner(
            session,
            enquiry_id,
            expected_version=expected_version,
            new_owner_id=new_owner.id,
        )
        if updated == 0:
            raise VersionConflict(
                f"enquiry {enquiry_id} changed under the owner update"
            )

        open_task = task_repo.get_open_task_for_update(session, enquiry_id)
        if open_task is not None:
            task_repo.update_task_owner(
                session, open_task.id, new_owner_id=new_owner.id
            )

        enquiry_repo.insert_owner_changed_event(
            session,
            enquiry_id=enquiry_id,
            operator_id=operator_id,
            from_owner_code=enquiry.owner_code,
            to_owner_code=new_owner.code,
            from_owner_id=enquiry.owner_id,
            to_owner_id=new_owner.id,
        )
