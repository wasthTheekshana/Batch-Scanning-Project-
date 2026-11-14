"""Users, sign-in protection and the append-only audit log.

A mixin of data.db.Database: it relies on the connection (`self.conn`) and the lock (`self._lock`) that class provides.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from data import timeutil


class UsersRepo:
    conn: sqlite3.Connection         # provided by Database
    _lock: threading.RLock

    def insert_user(self, user_id: str, username: str, password_hash: str,
                    role: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO users (user_id, username, password_hash, role) "
                "VALUES (?,?,?,?)", (user_id, username, password_hash, role))

    def get_user(self, username: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def set_password_hash(self, username: str, password_hash: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET password_hash=? WHERE username=?",
                (password_hash, username))

    def set_user_active(self, username: str, active: bool) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET active=? WHERE username=?",
                (1 if active else 0, username))

    def set_user_role(self, username: str, role: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET role=? WHERE username=?", (role, username))

    def list_users(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM users ORDER BY username").fetchall()

    def record_login_failure(self, username: str, max_failures: int,
                             lock_seconds: int) -> tuple[int, str | None]:
        """Count one more wrong password; on reaching `max_failures` lock the account for
        `lock_seconds`. Returns (failures so far, locked_until or None)."""
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT failed_logins FROM users WHERE username=?", (username,)).fetchone()
            if row is None:
                return 0, None
            failures = int(row["failed_logins"]) + 1
            locked_until = None
            if failures >= max_failures:
                locked_until = (datetime.now(timezone.utc)
                                + timedelta(seconds=lock_seconds)).isoformat()
                failures = 0                    # the lock is the penalty; count afresh after it
            self.conn.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE username=?",
                              (failures, locked_until, username))
            return failures, locked_until

    def clear_login_failures(self, username: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET failed_logins=0, locked_until=NULL WHERE username=?", (username,))

    def touch_login(self, username: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE users SET last_login=? WHERE username=?",
                (timeutil._now(), username))

    def append_audit(self, document_id: str | None, user: str | None,
                     action: str, detail: dict | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO audit_log (document_id, user, action, detail, timestamp) "
                "VALUES (?,?,?,?,?)",
                (document_id, user, action, json.dumps(detail or {}), timeutil._now()))
