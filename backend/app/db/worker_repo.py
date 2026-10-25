"""Repository for worker heartbeats (DB clock owns last_seen_at)."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import tables


def upsert_heartbeat(session: Session, worker_id: uuid.UUID, active_job_count: int) -> None:
    stmt = (
        pg_insert(tables.worker_heartbeats)
        .values(worker_id=worker_id, active_job_count=active_job_count)
        .on_conflict_do_update(
            index_elements=["worker_id"],
            set_={
                "last_seen_at": text("clock_timestamp()"),
                "active_job_count": active_job_count,
            },
        )
    )
    session.execute(stmt)


def get_heartbeat(session: Session, worker_id: uuid.UUID):
    return session.execute(
        select(
            tables.worker_heartbeats.c.worker_id,
            tables.worker_heartbeats.c.started_at,
            tables.worker_heartbeats.c.last_seen_at,
            tables.worker_heartbeats.c.active_job_count,
        ).where(tables.worker_heartbeats.c.worker_id == worker_id)
    ).one_or_none()
