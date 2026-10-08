"""Service configuration, read from the environment (no secrets in code)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    session_secret: str
    cookie_secure: bool = True
    enrollment_code_ttl_minutes: int = 60
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    default_poll_interval_seconds: int = 600

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ.get("CENTRAL_SESSION_SECRET", "")
        if len(secret) < 32:
            raise RuntimeError(
                "CENTRAL_SESSION_SECRET must be set to at least 32 characters")
        return cls(
            database_url=os.environ["CENTRAL_DATABASE_URL"],
            session_secret=secret,
            cookie_secure=os.environ.get("CENTRAL_COOKIE_SECURE", "1") != "0")
