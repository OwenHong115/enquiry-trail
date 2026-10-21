"""Provider health: operator resume seam (S6c; behaviour in Green)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ports.clock import BusinessClock
from app.ports.crm import CrmPort


def resume_provider(
    session: Session,
    provider: str,
    crm: CrmPort,
    clock: BusinessClock,
    *,
    operator_id: uuid.UUID,
    reason: str,
) -> bool:
    """Probe a blocked provider and requeue its jobs when healthy.

    Returns True when the probe passed and the provider resumed,
    False when the probe failed and the provider stays blocked. A
    failed probe is an expected outcome, not an exception; callers
    distinguish the two by this return value.
    """
    raise NotImplementedError
