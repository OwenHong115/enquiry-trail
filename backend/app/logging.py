"""Structured logging: one JSON object per record.

The message itself is the JSON string, so any handler or capture
mechanism (including pytest's caplog) sees the same shape. No
handlers are installed and no level is set here, and propagation
is left enabled: configuration belongs to the process entrypoint,
never to this module. Importing it has no side effects.

Callers must pass identifiers and outcomes only: never secrets,
session or CSRF values, raw email or phone, or raw CRM fields.
"""

from __future__ import annotations

import json
import logging

def log_event(event: str, **fields: object) -> None:
    """Emit one structured record whose message is a JSON object.

    The one application logger ("leadops") is fetched at call
    time, not bound at import: Alembic's fileConfig disables
    loggers that already exist when a migration runs, and an
    import-time logger would stay disabled for the process.
    """
    logging.getLogger("leadops").info(
        json.dumps({"event": event, **fields}, default=str)
    )
