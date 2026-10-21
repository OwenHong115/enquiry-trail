"""Follow-up task terminal-action seam (S4a; behaviour in Green)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ports.clock import BusinessClock


class TerminalConflict(Exception):
    """The task is already in the opposite terminal state."""


def complete_task(
    session: Session,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    reason: str,
    clock: BusinessClock,
) -> None:
    """Complete an open follow-up task."""
    raise NotImplementedError


def cancel_task(
    session: Session,
    task_id: uuid.UUID,
    *,
    expected_version: int,
    reason: str,
    clock: BusinessClock,
) -> None:
    """Cancel an open follow-up task."""
    raise NotImplementedError
