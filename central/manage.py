"""Operator CLI.  Usage: python -m central.manage create-admin USERNAME"""
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


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
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
