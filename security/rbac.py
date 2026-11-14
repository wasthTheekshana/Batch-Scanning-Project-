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
from datetime import datetime, timezone

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


MAX_FAILED_LOGINS = 5            # consecutive wrong passwords that lock an account
LOCK_SECONDS = 15 * 60           # ... for this long


class AccountLocked(AuthError):
    """Too many wrong passwords: the account is locked for a while."""

    def __init__(self, minutes: int):
        self.minutes = minutes
        super().__init__(f"account locked after too many wrong passwords; "
                         f"try again in {minutes} minute(s) or ask an administrator to unlock it")


def _minutes_left(locked_until: str | None) -> int:
    if not locked_until:
        return 0
    try:
        until = datetime.fromisoformat(locked_until)
    except ValueError:
        return 0
    seconds = (until - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(seconds // 60) + (1 if seconds % 60 else 0)) if seconds > 0 else 0


MIN_PASSWORD_LENGTH = 8
DEFAULT_PASSWORD = "admin"   # the seeded admin's first-run password


def validate_new_password(password: str, username: str = "") -> None:
    """Raise ValueError if `password` is not acceptable for a stored account."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.lower() in (DEFAULT_PASSWORD, username.lower()):
        raise ValueError("password is too easy to guess")


@dataclass(frozen=True)
class User:
    user_id: str
    username: str
    role: str
    # True when this sign-in used the default password; the app must make the
    # user choose a new one before anything else (see ui.login_dialog).
    must_change_password: bool = False

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
        left = _minutes_left(row["locked_until"])
        if left:
            db.append_audit(None, username, "login_blocked", {"minutes_left": left})
            raise AccountLocked(left)
        if not verify_password(password, row["password_hash"]):
            failures, locked_until = db.record_login_failure(
                username, MAX_FAILED_LOGINS, LOCK_SECONDS)
            db.append_audit(None, username, "login_failed", {"failures": failures})
            if locked_until:
                db.append_audit(None, username, "account_locked",
                                {"minutes": LOCK_SECONDS // 60})
                raise AccountLocked(LOCK_SECONDS // 60)
            raise AuthError("invalid password")
        db.clear_login_failures(username)
        if is_legacy_hash(row["password_hash"]):
            # self-migrating: upgrade Phase 1 sha256 hash to bcrypt
            db.set_password_hash(username, hash_password(password))
        db.touch_login(username)
        db.append_audit(None, username, "login", {"role": row["role"]})
        cls._current = User(row["user_id"], row["username"], row["role"],
                            must_change_password=(password == DEFAULT_PASSWORD))
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

def _require(actor: User | None, permission: str) -> User:
    """The actor, once known to hold `permission`; AuthError otherwise."""
    if actor is None or not actor.can(permission):
        raise AuthError(f"permission denied: {permission}")
    return actor


def create_user(db, actor: User | None, username: str, password: str,
                role: str) -> str:
    actor = _require(actor, "user.manage")
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"unknown role: {role}")
    if db.get_user(username) is not None:
        raise ValueError(f"user already exists: {username}")
    validate_new_password(password, username)
    user_id = str(uuid.uuid4())
    db.insert_user(user_id, username, hash_password(password), role)
    db.append_audit(None, actor.username, "user_create",
                    {"username": username, "role": role})
    return user_id


def unlock_user(db, actor: User | None, username: str) -> None:
    """Let a locked account sign in again now (an administrator's decision)."""
    actor = _require(actor, "user.manage")
    db.clear_login_failures(username)
    db.append_audit(None, actor.username, "account_unlocked", {"username": username})


def is_locked(db, username: str) -> bool:
    row = db.get_user(username)
    return bool(row is not None and _minutes_left(row["locked_until"]))


def verify_session_password(db, username: str, password: str) -> bool:
    """Is this the signed-in user's password? Used to unlock a session that went idle. Wrong
    attempts count towards the same lock as at sign-in."""
    row = db.get_user(username)
    if row is None or _minutes_left(row["locked_until"]):
        return False
    if verify_password(password, row["password_hash"]):
        db.clear_login_failures(username)
        return True
    failures, locked_until = db.record_login_failure(username, MAX_FAILED_LOGINS, LOCK_SECONDS)
    db.append_audit(None, username, "login_failed", {"failures": failures, "where": "session_unlock"})
    if locked_until:
        db.append_audit(None, username, "account_locked", {"minutes": LOCK_SECONDS // 60})
    return False


def set_user_active(db, actor: User | None, username: str,
                    active: bool) -> None:
    actor = _require(actor, "user.manage")
    db.set_user_active(username, active)
    db.append_audit(None, actor.username, "user_set_active",
                    {"username": username, "active": active})


def set_user_role(db, actor: User | None, username: str, role: str) -> None:
    actor = _require(actor, "user.manage")
    if role not in ROLE_PERMISSIONS:
        raise ValueError(f"unknown role: {role}")
    db.set_user_role(username, role)
    db.append_audit(None, actor.username, "user_set_role",
                    {"username": username, "role": role})


def reset_password(db, actor: User | None, username: str,
                   new_password: str) -> None:
    actor = _require(actor, "user.manage")
    validate_new_password(new_password, username)
    db.set_password_hash(username, hash_password(new_password))
    db.append_audit(None, actor.username, "password_reset",
                    {"username": username})


def change_password(db, username: str, old_password: str,
                    new_password: str) -> None:
    """Self-service change — requires knowing the current password."""
    row = db.get_user(username)
    if row is None or not verify_password(old_password, row["password_hash"]):
        raise AuthError("current password incorrect")
    validate_new_password(new_password, username)
    db.set_password_hash(username, hash_password(new_password))
    db.append_audit(None, username, "password_change", {})
