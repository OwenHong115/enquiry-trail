"""Seed reference data: `python -m app.seed`.

Idempotent: owners come from the routing config and are inserted
once; the operator is created from SEED_OPERATOR_USERNAME /
SEED_OPERATOR_PASSWORD only when absent — an existing operator's
password is never reset. Without SEED_OPERATOR_PASSWORD no operator
is created (and no default password is invented).
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

from app.config.routing import ROUTING_CONFIG
from app.db import seed_repo
from app.db.session import make_engine, make_session_factory
from app.logging import configure_logging, log_event
from app.security.passwords import hash_password
from app.settings import Settings


def seed(session: Session, *, operator_username: str = "demo-operator",
         operator_password: str = "") -> dict:
    owners = ROUTING_CONFIG["owners"]
    with session.begin():
        for owner in owners:
            seed_repo.upsert_owner(session, owner["code"], owner["display_name"])
        operator_created = False
        if operator_password:
            existing = seed_repo.get_operator_by_username(session, operator_username)
            if existing is None:
                seed_repo.insert_operator(
                    session, operator_username, hash_password(operator_password)
                )
                operator_created = True
    return {"owners": len(owners), "operator_created": operator_created}


def main() -> None:  # pragma: no cover - process entrypoint
    configure_logging()
    settings = Settings.from_env()
    engine = make_engine(settings.database_url)
    try:
        factory = make_session_factory(engine)
        session = factory()
        try:
            result = seed(
                session,
                operator_username=os.environ.get("SEED_OPERATOR_USERNAME", "demo-operator"),
                operator_password=os.environ.get("SEED_OPERATOR_PASSWORD", ""),
            )
        finally:
            session.close()
        log_event("seed.completed", owners=result["owners"],
                  operator_created=result["operator_created"])
    finally:
        engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    main()
