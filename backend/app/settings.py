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

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set")
        return cls(
            database_url=database_url,
            app_base_url=os.environ.get("APP_BASE_URL", "http://127.0.0.1:8080"),
            session_secret=os.environ.get("SESSION_SECRET", ""),
            crm_mode=os.environ.get("CRM_MODE", "mock"),
            crm_base_url=os.environ.get("CRM_BASE_URL", ""),
            crm_token=os.environ.get("CRM_TOKEN", ""),
        )
