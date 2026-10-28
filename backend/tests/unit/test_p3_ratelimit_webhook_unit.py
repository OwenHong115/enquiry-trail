"""P3b/P3c unit: rate-limit subject hashing, settings, webhook signatures."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.api.webhook import build_signature, verify_signature
from app.security.ratelimit import subject_hash
from app.settings import Settings

DB = "postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops"


def _settings(**kwargs) -> Settings:
    return Settings(database_url=DB, session_secret="s", **kwargs)


def test_subject_hash_is_sha256_of_ip_and_stable() -> None:
    expected = hashlib.sha256(b"203.0.113.9").hexdigest()
    assert subject_hash("203.0.113.9") == expected
    assert subject_hash("203.0.113.9") == subject_hash("203.0.113.9")
    assert subject_hash("203.0.113.10") != expected
    assert len(subject_hash(None)) == 64


def test_settings_rate_limit_and_webhook_defaults() -> None:
    settings = _settings()
    assert settings.rate_limit_window_seconds == 60
    assert settings.rate_limit_intake_max > 0
    assert settings.rate_limit_login_max > 0
    assert settings.webhook_replay_window_seconds == 300
    assert settings.webhook_secret == ""


@pytest.mark.parametrize("kwargs", [
    {"rate_limit_intake_max": 0},
    {"rate_limit_login_max": -1},
    {"rate_limit_window_seconds": 0},
    {"webhook_replay_window_seconds": 0},
    {"rate_limit_intake_max": True},
])
def test_settings_invalid_rate_limit_values_raise(kwargs) -> None:
    with pytest.raises(ValueError):
        _settings(**kwargs)


def test_settings_from_env_parses_p3bc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setenv("RATE_LIMIT_INTAKE_MAX", "7")
    monkeypatch.setenv("RATE_LIMIT_LOGIN_MAX", "3")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "120")
    monkeypatch.setenv("WEBHOOK_SECRET", "whsec-test")
    monkeypatch.setenv("WEBHOOK_REPLAY_WINDOW_SECONDS", "600")
    settings = Settings.from_env()
    assert settings.rate_limit_intake_max == 7
    assert settings.rate_limit_login_max == 3
    assert settings.rate_limit_window_seconds == 120
    assert settings.webhook_secret == "whsec-test"
    assert settings.webhook_replay_window_seconds == 600


def test_webhook_signature_roundtrip_and_tamper() -> None:
    body = b'{"first_name":"Alex"}'
    signature = build_signature("whsec-test", 1700000000, body)
    expected = hmac.new(
        b"whsec-test", b"1700000000." + body, hashlib.sha256
    ).hexdigest()
    assert signature == f"sha256={expected}"
    assert verify_signature("whsec-test", 1700000000, body, signature)
    assert not verify_signature("whsec-test", 1700000000, body + b" ", signature)
    assert not verify_signature("other-secret", 1700000000, body, signature)
    assert not verify_signature("whsec-test", 1700000001, body, signature)
    assert not verify_signature("whsec-test", 1700000000, body, "sha256=bad")
    assert not verify_signature("", 1700000000, body, signature)
