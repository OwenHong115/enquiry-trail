"""Review resolution: clear an enquiry's review flag, audited."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db import review_repo
from app.ports.clock import BusinessClock
from app.services.errors import VersionConflict

__all__ = ["VersionConflict", "resolve_review"]


def resolve_review(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    expected_version: int,
    operator_id: uuid.UUID,
    resolution_note: str,
    clock: BusinessClock,
) -> None:
    """Resolve an enquiry's review without changing its owner.

    One transaction: a conditional update keyed on the expected
    version, then exactly one review.resolved event. Zero updated
    rows means a missing enquiry (ValueError) or a version conflict
    (VersionConflict), with nothing written either way.
    """
    resolved_at = clock.now_utc()
    with session.begin():
        updated = review_repo.conditional_resolve(
            session,
            enquiry_id,
            expected_version=expected_version,
            operator_id=operator_id,
            resolved_at=resolved_at,
        )
        if updated == 0:
            current = review_repo.get_enquiry_version(session, enquiry_id)
            if current is None:
                raise ValueError(f"enquiry not found: {enquiry_id}")
            raise VersionConflict(
                f"enquiry {enquiry_id} is at version {current}, "
                f"not {expected_version}"
            )
        review_repo.insert_review_resolved_event(
            session,
            enquiry_id=enquiry_id,
            operator_id=operator_id,
            resolution_note=resolution_note,
        )
