"""Projects, centrally managed projects and project templates.

A mixin of data.db.Database: it relies on the connection (`self.conn`) and the lock (`self._lock`) that class provides.
"""
from __future__ import annotations

import json
import sqlite3
import threading

from data import timeutil


class ProjectsRepo:
    conn: sqlite3.Connection         # provided by Database
    _lock: threading.RLock

    def insert_project(self, project_id: str, name: str, config: dict,
                       version: int = 1, status: str = "active",
                       cloned_from: str | None = None) -> None:
        now = timeutil._now()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?)",
                (project_id, name, version, status, json.dumps(config),
                 cloned_from, now, now))

    def update_project(self, project_id: str, name: str, config: dict,
                       version: int, status: str = "active") -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE projects SET name=?, config_json=?, version=?, "
                "status=?, updated_at=? WHERE project_id=?",
                (name, json.dumps(config), version, status, timeutil._now(), project_id))

    def get_project(self, project_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM projects WHERE project_id=?",
                (project_id,)).fetchone()

    def list_projects(self, status: str | None = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM projects"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        with self._lock:
            return self.conn.execute(q + " ORDER BY name", args).fetchall()

    def set_central_project(self, project_id: str, central_version: int,
                            managed: bool = True) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO central_projects (project_id, central_version, "
                "managed, applied_at) VALUES (?,?,?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET "
                "central_version=excluded.central_version, "
                "managed=excluded.managed, applied_at=excluded.applied_at",
                (project_id, central_version, 1 if managed else 0, timeutil._now()))

    def get_central_project(self, project_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM central_projects WHERE project_id=?",
                (project_id,)).fetchone()

    def list_central_projects(self, managed_only: bool = False
                              ) -> list[sqlite3.Row]:
        q = "SELECT * FROM central_projects"
        if managed_only:
            q += " WHERE managed=1"
        with self._lock:
            return self.conn.execute(q + " ORDER BY project_id").fetchall()

    def unmanage_central_project(self, project_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE central_projects SET managed=0 WHERE project_id=?",
                (project_id,))

    def unmanage_all_central_projects(self) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE central_projects SET managed=0")

    def insert_template(self, template_id: str, name: str, category: str,
                        config: dict, built_in: bool = False) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO project_templates (template_id, name, "
                "category, config_json, built_in, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (template_id, name, category, json.dumps(config),
                 int(built_in), timeutil._now()))

    def get_template(self, template_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM project_templates WHERE template_id=?",
                (template_id,)).fetchone()

    def list_templates(self) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(
                "SELECT * FROM project_templates "
                "ORDER BY category, name").fetchall()

    def update_template_config(self, template_id: str, config: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE project_templates SET config_json=? "
                "WHERE template_id=?", (json.dumps(config), template_id))
