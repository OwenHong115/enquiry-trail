"""Application settings, read from the environment."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _parse_origin(raw: str) -> str:
    """Validate and normalise one exact origin, or raise ValueError.

    An origin is scheme://host[:port] only: http(s), no path (not
    even "/"), no query/fragment, no userinfo, no wildcard.
    """
    value = raw.strip()
    if not value or value == "null" or "*" in value or value.endswith("/"):
        raise ValueError(f"invalid origin: {raw!r}")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"invalid origin: {raw!r}")
    if parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError(f"invalid origin: {raw!r}")
    try:
        port = parsed.port
    except ValueError as err:
        raise ValueError(f"invalid origin: {raw!r}") from err
    host = parsed.hostname.lower()
    origin = f"{parsed.scheme}://{host}"
    if port is not None:
        origin = f"{origin}:{port}"
    return origin


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_base_url: str = "http://127.0.0.1:8080"
    session_secret: str = ""
    worker_concurrency: int = 2
    worker_idle_poll_seconds: float = 1.0
    worker_heartbeat_seconds: float = 5.0
    worker_shutdown_timeout_seconds: float = 30.0
    worker_error_backoff_seconds: float = 1.0
    crm_mode: str = "mock"
    crm_base_url: str = ""
    crm_token: str = ""
    # P3a ingress hardening. An empty allowed_origins tuple means
    # "derive the single origin from APP_BASE_URL".
    allowed_origins: tuple[str, ...] = ()
    max_request_bytes: int = 65536
    trusted_proxies: tuple[str, ...] = ()
    rate_limit_intake_max: int = 600
    rate_limit_login_max: int = 300
    rate_limit_window_seconds: int = 60
    webhook_secret: str = ""
    webhook_replay_window_seconds: int = 300

    def __post_init__(self) -> None:
        # Fail fast: an empty secret makes CSRF tokens computable
        # by anyone, so the app must not boot without a real one.
        if not self.session_secret.strip():
            raise ValueError("SESSION_SECRET must not be empty")
        if self.crm_mode not in ("mock", "http"):
            raise ValueError("CRM_MODE must be mock or http")
        # In http mode missing config fails startup; never fall back.
        if self.crm_mode == "http" and not (
            self.crm_base_url.strip() and self.crm_token.strip()
        ):
            raise ValueError("CRM_BASE_URL and CRM_TOKEN are required in http mode")
        if self.crm_base_url.strip():
            parsed = urlparse(self.crm_base_url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("CRM_BASE_URL must be an http(s) URL with a host")
        base = urlparse(self.app_base_url)
        if base.scheme not in ("http", "https") or not base.hostname:
            raise ValueError("APP_BASE_URL must be an http(s) URL with a host")
        if base.query or base.fragment:
            raise ValueError("APP_BASE_URL must not carry a query or fragment")
        for origin in self.allowed_origins:
            _parse_origin(origin)
        if isinstance(self.max_request_bytes, bool) or not isinstance(
            self.max_request_bytes, int
        ) or self.max_request_bytes <= 0:
            raise ValueError("MAX_REQUEST_BYTES must be a positive integer")
        for _rl_name, _rl_value in (("RATE_LIMIT_INTAKE_MAX", self.rate_limit_intake_max), ("RATE_LIMIT_LOGIN_MAX", self.rate_limit_login_max), ("RATE_LIMIT_WINDOW_SECONDS", self.rate_limit_window_seconds), ("WEBHOOK_REPLAY_WINDOW_SECONDS", self.webhook_replay_window_seconds)):
            if isinstance(_rl_value, bool) or not isinstance(_rl_value, int) or _rl_value <= 0:
                raise ValueError(f"{_rl_name} must be a positive integer")
        for proxy in self.trusted_proxies:
            self._parse_proxy(proxy)
        if not (1 <= self.worker_concurrency <= 16):
            raise ValueError("WORKER_CONCURRENCY must be between 1 and 16")
        for name, value in (
            ("WORKER_IDLE_POLL_SECONDS", self.worker_idle_poll_seconds),
            ("WORKER_HEARTBEAT_SECONDS", self.worker_heartbeat_seconds),
            ("WORKER_SHUTDOWN_TIMEOUT_SECONDS", self.worker_shutdown_timeout_seconds),
            ("WORKER_ERROR_BACKOFF_SECONDS", self.worker_error_backoff_seconds),
        ):
            if not (0.05 <= value <= 3600):
                raise ValueError(f"{name} must be between 0.05 and 3600 seconds")

    @staticmethod
    def _parse_proxy(raw: str):
        try:
            return ipaddress.ip_network(raw.strip(), strict=False)
        except ValueError as err:
            raise ValueError(f"invalid trusted proxy: {raw!r}") from err

    def resolved_allowed_origins(self) -> tuple[str, ...]:
        """Configured origins, or the one derived from APP_BASE_URL.

        Derivation uses scheme/host/port only: an APP_BASE_URL
        path or trailing slash is deployment decoration and is
        ignored here, never a startup-time surprise. Explicit
        ALLOWED_ORIGINS entries remain strict exact origins.
        """
        if self.allowed_origins:
            return tuple(_parse_origin(o) for o in self.allowed_origins)
        base = urlparse(self.app_base_url)
        origin = f"{base.scheme}://{base.hostname.lower()}"
        if base.port is not None:
            origin = f"{origin}:{base.port}"
        return (origin,)

    def trusted_proxy_networks(self) -> tuple:
        return tuple(self._parse_proxy(p) for p in self.trusted_proxies)

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set")
        allowed_raw = os.environ.get("ALLOWED_ORIGINS")
        # Unset or exactly empty (after strip) derives from
        # APP_BASE_URL; a non-empty value that yields no entries
        # (whitespace/commas only) is a configuration error.
        if (
            allowed_raw is not None
            and allowed_raw.strip() != ""
            and not _split_csv(allowed_raw)
        ):
            raise ValueError("ALLOWED_ORIGINS must not be an empty list")
        try:
            max_bytes = int(os.environ.get("MAX_REQUEST_BYTES", "65536"))
        except ValueError as err:
            raise ValueError("MAX_REQUEST_BYTES must be a positive integer") from err
        return cls(
            database_url=database_url,
            app_base_url=os.environ.get("APP_BASE_URL", "http://127.0.0.1:8080"),
            session_secret=os.environ.get("SESSION_SECRET", ""),
            worker_concurrency=int(os.environ.get("WORKER_CONCURRENCY", "2")),
            worker_idle_poll_seconds=float(os.environ.get("WORKER_IDLE_POLL_SECONDS", "1")),
            worker_heartbeat_seconds=float(os.environ.get("WORKER_HEARTBEAT_SECONDS", "5")),
            worker_shutdown_timeout_seconds=float(os.environ.get("WORKER_SHUTDOWN_TIMEOUT_SECONDS", "30")),
            worker_error_backoff_seconds=float(os.environ.get("WORKER_ERROR_BACKOFF_SECONDS", "1")),
            crm_mode=os.environ.get("CRM_MODE", "mock"),
            crm_base_url=os.environ.get("CRM_BASE_URL", ""),
            crm_token=os.environ.get("CRM_TOKEN", ""),
            allowed_origins=_split_csv(allowed_raw) if allowed_raw else (),
            max_request_bytes=max_bytes,
            trusted_proxies=_split_csv(os.environ.get("TRUSTED_PROXIES", "")),
            rate_limit_intake_max=int(os.environ.get("RATE_LIMIT_INTAKE_MAX", "600")),
            rate_limit_login_max=int(os.environ.get("RATE_LIMIT_LOGIN_MAX", "300")),
            rate_limit_window_seconds=int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60")),
            webhook_secret=os.environ.get("WEBHOOK_SECRET", ""),
            webhook_replay_window_seconds=int(os.environ.get("WEBHOOK_REPLAY_WINDOW_SECONDS", "300")),
        )
