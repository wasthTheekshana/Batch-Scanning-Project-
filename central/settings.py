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
    data_key: object = None                  # a central.crypto.DataKey, or None when none is configured
    alert_notifier: bool = False             # run the background worker that sends alerts to the webhook
    alert_interval_seconds: int = 60
    webhook_allow_http: bool = False         # development only: allow http:// webhook addresses
    webhook_block_private: bool = False      # refuse webhook addresses on private networks as well

    @classmethod
    def from_env(cls) -> "Settings":
        secret = os.environ.get("CENTRAL_SESSION_SECRET", "")
        if len(secret) < 32:
            raise RuntimeError(
                "CENTRAL_SESSION_SECRET must be set to at least 32 characters")
        from central.crypto import load_data_key
        return cls(
            database_url=os.environ["CENTRAL_DATABASE_URL"],
            session_secret=secret,
            cookie_secure=os.environ.get("CENTRAL_COOKIE_SECURE", "1") != "0",
            data_key=load_data_key(),
            alert_notifier=os.environ.get("CENTRAL_ALERT_NOTIFIER", "1") != "0",
            alert_interval_seconds=int(os.environ.get("CENTRAL_ALERT_INTERVAL", "60")),
            webhook_allow_http=os.environ.get("CENTRAL_WEBHOOK_ALLOW_HTTP") == "1",
            webhook_block_private=os.environ.get("CENTRAL_WEBHOOK_BLOCK_PRIVATE") == "1")
