"""Database engine/session wiring and the FastAPI dependency."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


# Bounds every connection from this factory, so the readiness
# probe (and any other connect) cannot hang on a dead host.
CONNECT_TIMEOUT_SECONDS = 2
# Server-side statement bound for every connection from this
# factory: connect_timeout alone cannot bound a statement that
# stalls after connecting (the R7b/R13 readiness bound).
STATEMENT_TIMEOUT_MS = 1000
POOL_TIMEOUT_SECONDS = 1


def make_engine(database_url: str):
    return create_engine(
        database_url,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        },
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
