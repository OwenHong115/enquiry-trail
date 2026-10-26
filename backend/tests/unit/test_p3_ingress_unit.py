"""P3a unit: Settings validation and trusted-proxy client IP."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.security.proxy import effective_client_ip
from app.settings import Settings

DB = "postgresql+psycopg://leadops:leadops@127.0.0.1:55432/leadops"


def _settings(**kwargs) -> Settings:
    return Settings(database_url=DB, session_secret="s", **kwargs)


def _request(peer: str | None, xff: str | None = None) -> Request:
    headers = [(b"host", b"testserver")]
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 12345) if peer is not None else None,
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


def test_settings_defaults_derive_origin_from_app_base_url() -> None:
    settings = _settings()
    assert settings.resolved_allowed_origins() == ("http://127.0.0.1:8080",)
    assert settings.max_request_bytes == 65536
    assert settings.trusted_proxies == ()


def test_settings_explicit_allowed_origins() -> None:
    settings = _settings(
        allowed_origins=("https://app.example.com", "http://localhost:3000")
    )
    assert settings.resolved_allowed_origins() == (
        "https://app.example.com", "http://localhost:3000",
    )


@pytest.mark.parametrize("bad", [
    "app.example.com",
    "ftp://app.example.com",
    "https://app.example.com/",
    "https://app.example.com/path",
    "https://*.example.com",
    "https://",
    "",
    "null",
])
def test_settings_invalid_allowed_origin_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        _settings(allowed_origins=(bad,))


@pytest.mark.parametrize("bad", [0, -1, "65536", 1.5, True])
def test_settings_invalid_max_request_bytes_raises(bad) -> None:
    with pytest.raises(ValueError):
        _settings(max_request_bytes=bad)


@pytest.mark.parametrize("bad", ["not-an-ip", "999.1.1.1", "10.0.0.0/99", ""])
def test_settings_invalid_trusted_proxy_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        _settings(trusted_proxies=(bad,))


def test_settings_trusted_proxy_cidr_ok() -> None:
    settings = _settings(trusted_proxies=("10.0.0.0/8", "192.168.1.10"))
    networks = settings.trusted_proxy_networks()
    assert len(networks) == 2


def test_settings_invalid_app_base_url_raises() -> None:
    with pytest.raises(ValueError):
        _settings(app_base_url="ftp://example.com")
    with pytest.raises(ValueError):
        _settings(app_base_url="http://")


def test_settings_from_env_parses_p3a(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example.com, http://b.example.com:8080")
    monkeypatch.setenv("MAX_REQUEST_BYTES", "12345")
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    settings = Settings.from_env()
    assert settings.resolved_allowed_origins() == (
        "https://a.example.com", "http://b.example.com:8080",
    )
    assert settings.max_request_bytes == 12345
    assert settings.trusted_proxies == ("10.0.0.0/8",)


def test_settings_from_env_invalid_allowed_origins_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setenv("ALLOWED_ORIGINS", " , ")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_settings_from_env_empty_allowed_origins_derives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setenv("APP_BASE_URL", "http://app.example.com:9000")
    monkeypatch.setenv("ALLOWED_ORIGINS", "")
    settings = Settings.from_env()
    assert settings.resolved_allowed_origins() == ("http://app.example.com:9000",)


def test_app_base_url_trailing_slash_derives_origin() -> None:
    settings = _settings(app_base_url="http://127.0.0.1:8080/")
    assert settings.resolved_allowed_origins() == ("http://127.0.0.1:8080",)
    settings = _settings(app_base_url="https://example.com/base/")
    assert settings.resolved_allowed_origins() == ("https://example.com",)


def test_proxy_direct_peer_ignores_spoofed_xff() -> None:
    settings = _settings()
    request = _request("203.0.113.9", xff="198.51.100.7")
    assert effective_client_ip(request, settings.trusted_proxy_networks()) == "203.0.113.9"


def test_proxy_trusted_peer_uses_rightmost_untrusted() -> None:
    settings = _settings(trusted_proxies=("10.0.0.0/8",))
    # Chain: client, trusted hop; peer is the trusted proxy itself.
    request = _request("10.0.0.2", xff="198.51.100.7, 10.0.0.1")
    assert effective_client_ip(request, settings.trusted_proxy_networks()) == "198.51.100.7"
    # An untrusted middle hop wins over addresses to its left.
    request = _request("10.0.0.2", xff="198.51.100.7, 203.0.113.5, 10.0.0.1")
    assert effective_client_ip(request, settings.trusted_proxy_networks()) == "203.0.113.5"


def test_proxy_trusted_cidr_peer() -> None:
    settings = _settings(trusted_proxies=("10.0.0.0/8",))
    request = _request("10.1.2.3", xff="198.51.100.20")
    assert effective_client_ip(request, settings.trusted_proxy_networks()) == "198.51.100.20"


def test_proxy_malformed_xff_fails_safe_to_peer() -> None:
    settings = _settings(trusted_proxies=("10.0.0.0/8",))
    request = _request("10.0.0.2", xff="not-an-ip, 10.0.0.1")
    assert effective_client_ip(request, settings.trusted_proxy_networks()) == "10.0.0.2"


def test_proxy_missing_client_is_none() -> None:
    settings = _settings(trusted_proxies=("10.0.0.0/8",))
    assert effective_client_ip(_request(None, xff="1.2.3.4"),
                                settings.trusted_proxy_networks()) is None
