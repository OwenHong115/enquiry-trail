"""Worker claim and prepare seam (S4b; behaviour arrives in Green)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ports.clock import BusinessClock


@dataclass(frozen=True)
class ClaimedJob:
    """One claimed job and the lease that fences it."""

    job_id: uuid.UUID
    kind: str
    entity_id: uuid.UUID
    lease_token: uuid.UUID
    attempt_no: int


def claim_next(
    session: Session, worker_id: uuid.UUID, clock: BusinessClock
) -> ClaimedJob | None:
    """Claim the next due job, or return None when nothing is due."""
    raise NotImplementedError


def handle_claim(session: Session, claim: ClaimedJob, clock: BusinessClock) -> None:
    """Execute one claimed job (the local prepare for S4b)."""
    raise NotImplementedError
