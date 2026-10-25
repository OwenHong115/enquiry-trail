"""Application settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


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

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set")
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
        )
