"""RBAC (HLD §10.1, §17.5; Phase 2 §18 weeks 7-8).

Four roles with permission sets defined as plain data; every UI element
calls Session.current_user().can(...). Passwords use bcrypt; legacy
Phase 1 salted-sha256 hashes still verify and are transparently
re-hashed to bcrypt on the next successful login.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import bcrypt as _bcrypt

ALL_PERMISSIONS = {
    "scan.run", "batch.view_own", "batch.view_all", "qc.review",
    "project.edit", "user.manage", "report.view", "report.export",
    "config.backup", "custody.manage", "document.search",
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": set(ALL_PERMISSIONS),
    "operator": {"scan.run", "batch.view_own", "custody.manage",
                 "document.search"},
    "qc_officer": {"qc.review", "batch.view_all", "report.view",
                  "custody.manage", "document.search"},
    "viewer": {"batch.view_all", "report.view", "report.export",
               "document.search"},
}


class AuthError(Exception):
    """Bad credentials or inactive account."""


_LEGACY_SALT = "dok-scanner-v1"


def hash_password(password: str) -> str:
    """bcrypt, cost 12 (Phase 2). Legacy Phase 1 hashes were salted
    sha256; verify_password still accepts them for migration."""
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(12)).decode()


def _legacy_hash(password: str) -> str:
    return hashlib.sha256((_LEGACY_SALT + password).encode()).hexdigest()


def is_legacy_hash(password_hash: str) -> bool:
    return not password_hash.startswith("$2")


def verify_password(password: str, password_hash: str) -> bool:
    if is_legacy_hash(password_hash):
        return _legacy_hash(password) == password_hash
    try:
        return _bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


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
        if is_legacy_hash(row["password_hash"]):
            # self-migrating: upgrade Phase 1 sha256 hash to bcrypt
            db.set_password_hash(username, hash_password(password))
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


# -- user management (Admin panel backend, Phase 2) ---------------------------

def _require(actor: User | None, permission: str) -> None:
    if actor is None or not actor.can(permission):
        raise AuthError(f"permission denied: {permission}")


def create_user(db, actor: User | None, username: str, password: str,
                role: str) -> str:
    _require(actor, "user.manage")
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"unknown role: {role}")
    if db.get_user(username) is not None:
        raise ValueError(f"user already exists: {username}")
    user_id = str(uuid.uuid4())
    db.insert_user(user_id, username, hash_password(password), role)
    db.append_audit(None, actor.username, "user_create",
                    {"username": username, "role": role})
    return user_id


def set_user_active(db, actor: User | None, username: str,
                    active: bool) -> None:
    _require(actor, "user.manage")
    db.set_user_active(username, active)
    db.append_audit(None, actor.username, "user_set_active",
                    {"username": username, "active": active})


def set_user_role(db, actor: User | None, username: str, role: str) -> None:
    _require(actor, "user.manage")
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"unknown role: {role}")
    db.set_user_role(username, role)
    db.append_audit(None, actor.username, "user_set_role",
                    {"username": username, "role": role})


def reset_password(db, actor: User | None, username: str,
                   new_password: str) -> None:
    _require(actor, "user.manage")
    db.set_password_hash(username, hash_password(new_password))
    db.append_audit(None, actor.username, "password_reset",
                    {"username": username})


def change_password(db, username: str, old_password: str,
                    new_password: str) -> None:
    """Self-service change — requires knowing the current password."""
    row = db.get_user(username)
    if row is None or not verify_password(old_password, row["password_hash"]):
        raise AuthError("current password incorrect")
    db.set_password_hash(username, hash_password(new_password))
    db.append_audit(None, username, "password_change", {})
