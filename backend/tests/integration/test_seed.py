"""Seed repeatability: idempotent owners, operator never reset."""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.seed import seed
from app.security.passwords import verify_password


def _factory(postgres_db):
    return sessionmaker(bind=create_engine(postgres_db))


def _fetch(postgres_db, sql, params=None):
    engine = create_engine(postgres_db)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).all()
    finally:
        engine.dispose()


def test_seed_twice_idempotent_and_password_preserved(postgres_db):
    session = _factory(postgres_db)()
    try:
        first = seed(session, operator_username="demo-operator", operator_password="first-pass")
    finally:
        session.close()
    assert first == {"owners": 3, "operator_created": True}

    session = _factory(postgres_db)()
    try:
        second = seed(session, operator_username="demo-operator", operator_password="other-pass")
    finally:
        session.close()
    assert second == {"owners": 3, "operator_created": False}

    owners = _fetch(postgres_db, "SELECT code FROM leadops.owners ORDER BY code")
    assert [r.code for r in owners] == ["intake", "maya", "oliver"]
    operators = _fetch(postgres_db, "SELECT password_hash FROM leadops.operators WHERE username='demo-operator'")
    assert len(operators) == 1
    assert verify_password("first-pass", operators[0].password_hash)
    assert not verify_password("other-pass", operators[0].password_hash)


def test_seed_without_password_creates_no_operator(postgres_db):
    session = _factory(postgres_db)()
    try:
        result = seed(session)
    finally:
        session.close()
    assert result == {"owners": 3, "operator_created": False}
    assert _fetch(postgres_db, "SELECT id FROM leadops.operators") == []
