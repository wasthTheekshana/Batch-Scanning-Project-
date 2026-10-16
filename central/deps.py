"""Request dependencies: DB session, admin session, CSRF, PC API key."""
from __future__ import annotations

import hmac
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from central.models import AdminUser, Pc
from central.security import hash_secret

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# An auditor may read everything an admin can, and change nothing: the only
# writes allowed are those on their own account (sign out, own password).
READ_ONLY_ROLES = {"auditor"}
OWN_ACCOUNT_WRITES = {"/admin/logout", "/admin/me/password"}


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def require_admin(request: Request,
                  db: Session = Depends(get_db)) -> AdminUser:
    admin_id = request.session.get("admin_id")
    session_version = request.session.get("sv")
    user = db.get(AdminUser, admin_id) if admin_id else None
    if user is None or session_version != user.session_version:
        raise HTTPException(401, "login required")
    if (user.role in READ_ONLY_ROLES and request.method not in SAFE_METHODS
            and request.url.path not in OWN_ACCOUNT_WRITES):
        raise HTTPException(403, "this account is read-only")
    return user


def require_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    sent = request.headers.get("x-csrf-token", "").encode("utf-8")
    expected = str(request.session.get("csrf", "")).encode("utf-8")
    if not expected or not hmac.compare_digest(sent, expected):
        raise HTTPException(403, "missing or invalid CSRF token")


def require_pc(pc_id: str, request: Request,
               db: Session = Depends(get_db)) -> Pc:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "API key required")
    pc = db.scalar(select(Pc).where(
        Pc.api_key_hash == hash_secret(token.strip())))
    if pc is None:
        raise HTTPException(401, "invalid API key")
    if pc.id != pc_id:
        raise HTTPException(403, "API key does not belong to this PC")
    return pc
