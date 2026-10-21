"""Database engine/session wiring and the FastAPI dependency."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str):
    return create_engine(database_url)


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine)


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()
