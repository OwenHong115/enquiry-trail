"""Application settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_base_url: str = "http://127.0.0.1:8080"
    session_secret: str = ""

    def __post_init__(self) -> None:
        # Fail fast: an empty secret makes CSRF tokens computable
        # by anyone, so the app must not boot without a real one.
        if not self.session_secret.strip():
            raise ValueError("SESSION_SECRET must not be empty")

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is not set")
        return cls(
            database_url=database_url,
            app_base_url=os.environ.get("APP_BASE_URL", "http://127.0.0.1:8080"),
            session_secret=os.environ.get("SESSION_SECRET", ""),
        )
