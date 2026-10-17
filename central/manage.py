"""Operator CLI.

    python -m central.manage create-admin USERNAME [--role admin|auditor]
    python -m central.manage migrate          bring the database to the latest schema
    python -m central.manage db-status        show the schema version and any drift from the models
    python -m central.manage new-migration "message"   write a migration for a model change
"""
from __future__ import annotations

import getpass
import logging
import sys

from sqlalchemy import select

from central.audit import record
from central.db import make_engine, make_session_factory
from central.models import AdminUser
from central.security import hash_password
from central.settings import Settings

MIN_PASSWORD_LEN = 12
log = logging.getLogger("dokscanner.central")


def create_admin(session_factory, username: str, password: str,
                 role: str = "admin") -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(
            f"password must be at least {MIN_PASSWORD_LEN} characters")
    with session_factory() as db:
        if db.scalar(select(AdminUser).where(AdminUser.username == username)):
            raise ValueError(f"admin {username!r} already exists")
        db.add(AdminUser(username=username,
                         password_hash=hash_password(password), role=role))
        record(db, "cli", "admin_created",
               {"username": username, "role": role})
        db.commit()
    log.info("admin created: %s", username)


def _schema_command(argv: list[str]) -> int | None:
    """migrate / db-status / new-migration; None when argv is not one of them."""
    from central import migrate
    if not argv or argv[0] not in ("migrate", "db-status", "new-migration"):
        return None
    engine = make_engine(Settings.from_env().database_url)      # opening it migrates it
    if argv[0] == "migrate":
        print(f"schema is at {migrate.current_revision(engine)} (latest {migrate.head_revision()})")
    elif argv[0] == "db-status":
        drift = migrate.differences(engine)
        print(f"schema {migrate.current_revision(engine)}; latest {migrate.head_revision()}; "
              + ("matches the models" if not drift else f"DIFFERS from the models: {drift}"))
        return 1 if drift else 0
    else:
        if len(argv) != 2:
            print('usage: python -m central.manage new-migration "message"', file=sys.stderr)
            return 2
        print("wrote migration", migrate.new_migration(engine, argv[1]))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    handled = _schema_command(argv)
    if handled is not None:
        return handled
    role = "admin"
    if len(argv) == 4 and argv[2] == "--role":
        role, argv = argv[3], argv[:2]
    if len(argv) != 2 or argv[0] != "create-admin" or role not in ("admin", "auditor"):
        print("usage: python -m central.manage create-admin USERNAME [--role admin|auditor]",
              file=sys.stderr)
        return 2
    factory = make_session_factory(
        make_engine(Settings.from_env().database_url))
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat: "):
        print("passwords differ", file=sys.stderr)
        return 1
    try:
        create_admin(factory, argv[1], password, role=role)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"admin {argv[1]!r} created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
