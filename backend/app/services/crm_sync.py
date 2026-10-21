"""CRM contact sync seam (S5; behaviour arrives in Green)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ports.clock import BusinessClock
from app.ports.crm import CrmPort


def sync_contact(
    session: Session,
    enquiry_id: uuid.UUID,
    crm: CrmPort | None,
    clock: BusinessClock,
) -> None:
    """Sync the enquiry's contact to the CRM exactly once per claim."""
    raise NotImplementedError
