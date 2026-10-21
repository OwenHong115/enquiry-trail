"""Intake submission seam (S2; behaviour arrives in Green)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ports.clock import BusinessClock


def create_follow_up_task(
    session: Session,
    enquiry_id: uuid.UUID,
    *,
    owner_id: uuid.UUID,
    due_at: datetime,
) -> None:
    """Insert the initial follow-up task for an enquiry (barrier seam)."""
    raise NotImplementedError


def submit_intake(
    session: Session,
    payload: dict[str, Any],
    *,
    source: str,
    event_key: uuid.UUID,
    clock: BusinessClock,
    routing_config: dict[str, Any],
) -> None:
    """Persist one intake: receipt, contact, enquiry and follow-up task."""
    raise NotImplementedError
