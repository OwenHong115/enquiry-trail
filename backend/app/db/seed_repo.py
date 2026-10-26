"""Repository for seed data (idempotent reference rows)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import tables


def upsert_owner(session: Session, code: str, display_name: str) -> None:
    stmt = (
        pg_insert(tables.owners)
        .values(code=code, display_name=display_name)
        .on_conflict_do_nothing(index_elements=["code"])
    )
    session.execute(stmt)


def get_operator_by_username(session: Session, username: str):
    return session.execute(
        select(tables.operators.c.id, tables.operators.c.password_hash)
        .where(tables.operators.c.username == username)
    ).one_or_none()


def insert_operator(session: Session, username: str, password_hash: str) -> None:
    stmt = (
        pg_insert(tables.operators)
        .values(username=username, password_hash=password_hash)
        .on_conflict_do_nothing(index_elements=["username"])
    )
    session.execute(stmt)
