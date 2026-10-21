"""Review resolution seam (S4a; behaviour arrives in Green)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ports.clock import BusinessClock


class VersionConflict(Exception):
    """The enquiry version did not match the caller's expectation."""


def resolve_review(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    expected_version: int,
    operator_id: uuid.UUID,
    resolution_note: str,
    clock: BusinessClock,
) -> None:
    """Resolve an enquiry's review without changing its owner."""
    raise NotImplementedError
