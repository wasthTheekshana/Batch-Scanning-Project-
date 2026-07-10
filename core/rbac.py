"""RBAC skeleton (HLD §10.1, §17.5).

Four roles with permission sets defined as plain data. Every UI element
calls Session.current_user().can(...) from day one so Phase 2 only has to
swap the hash function to bcrypt and add the user-management panel.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

ALL_PERMISSIONS = {
    "scan.run", "batch.view_own", "batch.view_all", "qc.review",
    "project.edit", "user.manage", "report.view", "report.export",
    "config.backup",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": set(ALL_PERMISSIONS),
    "operator": {"scan.run", "batch.view_own"},
    "qc_officer": {"qc.review", "batch.view_all", "report.view"},
    "viewer": {"batch.view_all", "report.view", "report.export"},
}


class AuthError(Exception):
    """Bad credentials or inactive account."""


def hash_password(password: str) -> str:
    """Phase 1: salted sha256. Phase 2 replaces this with bcrypt —
    only this function and verify_password change."""
    salt = "dok-scanner-v1"
    return hashlib.sha256((salt + password).encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    return hash_password(password) == password_hash


@dataclass(frozen=True)
class User:
    user_id: str
    username: str
    role: str

    def can(self, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, set())


class Session:
    """Process-wide login session."""

    _current: User | None = None

    @classmethod
    def login(cls, username: str, password: str, db) -> User:
        cls.seed_default_admin(db)
        row = db.get_user(username)
        if row is None or not row["active"]:
            raise AuthError("unknown or inactive user")
        if not verify_password(password, row["password_hash"]):
            raise AuthError("invalid password")
        db.touch_login(username)
        db.append_audit(None, username, "login", {"role": row["role"]})
        cls._current = User(row["user_id"], row["username"], row["role"])
        return cls._current

    @classmethod
    def logout(cls, db=None) -> None:
        if cls._current and db:
            db.append_audit(None, cls._current.username, "logout", {})
        cls._current = None

    @classmethod
    def current_user(cls) -> User | None:
        return cls._current

    @staticmethod
    def seed_default_admin(db) -> None:
        """First run: create admin/admin so the app is usable out of the box."""
        if db.get_user("admin") is None:
            db.insert_user(str(uuid.uuid4()), "admin",
                           hash_password("admin"), "admin")
