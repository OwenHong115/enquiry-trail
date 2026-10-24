"""Database engine/session wiring and the FastAPI dependency."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


# Bounds every connection from this factory, so the readiness
# probe (and any other connect) cannot hang on a dead host.
CONNECT_TIMEOUT_SECONDS = 2


def make_engine(database_url: str):
    return create_engine(
        database_url,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine)


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()
