"""Admin: manage the admin accounts of the central service."""
import re
import threading
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from central.audit import record
from central.deps import get_db, require_admin, require_csrf
from central.models import AdminUser, SearcherScope, utcnow
from central.security import hash_password, verify_password

router = APIRouter(prefix="/admin", tags=["admin-users"],
                   dependencies=[Depends(require_admin),
                                 Depends(require_csrf)])

# Disabling or demoting an admin must see the other admins' committed state.
# The database row lock below does that on PostgreSQL across workers; this lock
# does it inside one process on any database (SQLite ignores row locks).
_ADMIN_CHANGE = threading.Lock()

MIN_PASSWORD_LEN = 12
# A disabled account is one locked "forever": the login path already treats a
# locked account exactly like an unknown one, and no schema change is needed.
DISABLED_UNTIL = datetime(9999, 12, 31)


def _password(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("password must be valid UTF-8")
    if len(value) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    return value


class UserIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=False)
    username: str = Field(pattern=r"^[A-Za-z0-9._@-]{3,100}$")
    password: str = Field(max_length=200)
    role: Literal["admin", "auditor", "searcher"] = "admin"

    @field_validator("password")
    @classmethod
    def strong_enough(cls, value):
        return _password(value)

    @field_validator("password")
    @classmethod
    def not_the_username(cls, value, info):
        if value.lower() == str(info.data.get("username", "")).lower():
            raise ValueError("password must differ from the username")
        return value


class RoleIn(BaseModel):
    role: Literal["admin", "auditor", "searcher"]


class ProjectsIn(BaseModel):
    project_ids: list[str] = Field(max_length=200)

    @field_validator("project_ids")
    @classmethod
    def _ids(cls, value):
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v) for v in value):
            raise ValueError("a project id is not valid")
        return sorted(set(value))


class PasswordIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=False)
    password: str = Field(max_length=200)

    @field_validator("password")
    @classmethod
    def strong_enough(cls, value):
        return _password(value)


class OwnPasswordIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=False)
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(max_length=200)

    @field_validator("new_password")
    @classmethod
    def strong_enough(cls, value):
        return _password(value)


def status_of(user: AdminUser) -> str:
    if user.locked_until and user.locked_until >= DISABLED_UNTIL:
        return "disabled"
    if user.locked_until and user.locked_until > utcnow():
        return "locked"
    return "active"


def _view(user: AdminUser, projects: list[str] | None = None) -> dict:
    locked = user.locked_until and status_of(user) == "locked"
    view = {"id": user.id, "username": user.username, "role": user.role,
            "status": status_of(user), "failed_count": user.failed_count,
            "locked_until": user.locked_until.isoformat() + "Z" if locked else None}
    if user.role == "searcher" and projects is not None:
        view["projects"] = projects
    return view


def active_admin_count(db: Session, excluding_id: int | None = None) -> int:
    """Admins (not auditors) who could still sign in (not disabled); locked
    ones count, a lockout expires by itself."""
    query = select(func.count()).select_from(AdminUser).where(
        AdminUser.role == "admin").where(
        (AdminUser.locked_until.is_(None))
        | (AdminUser.locked_until < DISABLED_UNTIL))
    if excluding_id is not None:
        query = query.where(AdminUser.id != excluding_id)
    return db.scalar(query)


def _get(db: Session, user_id: int) -> AdminUser:
    user = db.get(AdminUser, user_id)
    if user is None:
        raise HTTPException(404, "unknown admin user")
    return user


def _actor(admin: AdminUser) -> str:
    return f"admin:{admin.username}"


def _bump(user: AdminUser) -> None:
    """Invalidate every session of this user (sessions carry the version)."""
    user.session_version = (user.session_version or 0) + 1


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    scopes: dict[int, list[str]] = {}
    for admin_id, project_id in db.execute(select(SearcherScope.admin_id, SearcherScope.project_id)
                                           .order_by(SearcherScope.project_id)):
        scopes.setdefault(admin_id, []).append(project_id)
    return [_view(u, scopes.get(u.id, [])) for u in db.scalars(
        select(AdminUser).order_by(AdminUser.username))]


@router.put("/users/{user_id}/projects")
def set_projects(user_id: int, body: ProjectsIn, admin: AdminUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    """Which projects a searcher may see documents of (replaces the list). Only a searcher has one."""
    user = _get(db, user_id)
    if user.role != "searcher":
        raise HTTPException(409, "only a searcher has a list of projects")
    db.execute(delete(SearcherScope).where(SearcherScope.admin_id == user.id))
    db.add_all(SearcherScope(admin_id=user.id, project_id=p) for p in body.project_ids)
    record(db, _actor(admin), "searcher_projects_set", {"username": user.username, "id": user.id,
                                                         "count": len(body.project_ids)})
    db.commit()
    return _view(user, body.project_ids)


@router.post("/users", status_code=201)
def create_user(body: UserIn, admin: AdminUser = Depends(require_admin),
                db: Session = Depends(get_db)):
    if db.scalar(select(AdminUser).where(AdminUser.username == body.username)):
        raise HTTPException(409, "an admin with that username already exists")
    user = AdminUser(username=body.username,
                     password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "an admin with that username already exists")
    record(db, _actor(admin), "admin_user_created",
           {"username": user.username, "id": user.id, "role": user.role})
    db.commit()
    return _view(user)


# static paths first: "/users/revoke-all-sessions" must not reach "/users/{id}"
@router.post("/users/revoke-all-sessions", status_code=204)
def revoke_all_sessions(admin: AdminUser = Depends(require_admin),
                        db: Session = Depends(get_db)):
    db.execute(update(AdminUser).values(
        session_version=AdminUser.session_version + 1))
    record(db, _actor(admin), "admin_sessions_revoked", {"scope": "all"})
    db.commit()
    return Response(status_code=204)


@router.post("/users/{user_id}/disable")
def disable_user(user_id: int, admin: AdminUser = Depends(require_admin),
                 db: Session = Depends(get_db)):
    with _ADMIN_CHANGE:
        # Lock every admin row first (a handful): two admins disabling each
        # other at the same moment would otherwise each see the other as still
        # active and both succeed, leaving nobody able to sign in. The second
        # request waits here and then counts the first one's committed result.
        db.execute(select(AdminUser.id).with_for_update()).all()
        user = _get(db, user_id)
        if user.id == admin.id:
            raise HTTPException(409, "you cannot disable yourself")
        if active_admin_count(db, excluding_id=user.id) < 1:
            raise HTTPException(409, "at least one admin must stay enabled")
        user.locked_until = DISABLED_UNTIL
        user.failed_count = 0
        _bump(user)
        record(db, _actor(admin), "admin_user_disabled",
               {"username": user.username, "id": user.id})
        db.commit()
        return _view(user)


@router.post("/users/{user_id}/role")
def change_role(user_id: int, body: RoleIn, admin: AdminUser = Depends(require_admin),
                db: Session = Depends(get_db)):
    with _ADMIN_CHANGE:
        # same lock as disabling: two admins demoting each other must not both succeed
        db.execute(select(AdminUser.id).with_for_update()).all()
        user = _get(db, user_id)
        if user.id == admin.id:
            raise HTTPException(409, "you cannot change your own role")
        if user.role == "admin" and body.role != "admin"             and active_admin_count(db, excluding_id=user.id) < 1:
            raise HTTPException(409, "at least one admin must stay enabled")
        if user.role != body.role:
            user.role = body.role
            db.execute(delete(SearcherScope).where(SearcherScope.admin_id == user.id))
            _bump(user)                       # their open sessions pick up the new rights
            record(db, _actor(admin), "admin_role_changed",
                   {"username": user.username, "id": user.id, "role": body.role})
        db.commit()
        return _view(user)


@router.post("/users/{user_id}/enable")
def enable_user(user_id: int, admin: AdminUser = Depends(require_admin),
                db: Session = Depends(get_db)):
    user = _get(db, user_id)
    user.locked_until = None
    user.failed_count = 0
    record(db, _actor(admin), "admin_user_enabled",
           {"username": user.username, "id": user.id})
    db.commit()
    return _view(user)


@router.post("/users/{user_id}/unlock")
def unlock_user(user_id: int, admin: AdminUser = Depends(require_admin),
                db: Session = Depends(get_db)):
    user = _get(db, user_id)
    if status_of(user) == "disabled":
        raise HTTPException(409, "the account is disabled: enable it instead")
    user.locked_until = None
    user.failed_count = 0
    record(db, _actor(admin), "admin_user_unlocked",
           {"username": user.username, "id": user.id})
    db.commit()
    return _view(user)


@router.post("/users/{user_id}/password", status_code=204)
def reset_password(user_id: int, body: PasswordIn,
                   admin: AdminUser = Depends(require_admin),
                   db: Session = Depends(get_db)):
    user = _get(db, user_id)
    if body.password.lower() == user.username.lower():
        raise HTTPException(422, "password must differ from the username")
    user.password_hash = hash_password(body.password)
    user.failed_count = 0
    if status_of(user) == "locked":          # a reset ends a lockout, never a disable
        user.locked_until = None
    _bump(user)
    record(db, _actor(admin), "admin_password_reset",
           {"username": user.username, "id": user.id})
    db.commit()
    return Response(status_code=204)


@router.post("/users/{user_id}/revoke-sessions", status_code=204)
def revoke_sessions(user_id: int, admin: AdminUser = Depends(require_admin),
                    db: Session = Depends(get_db)):
    user = _get(db, user_id)
    _bump(user)
    record(db, _actor(admin), "admin_sessions_revoked",
           {"username": user.username, "id": user.id, "scope": "user"})
    db.commit()
    return Response(status_code=204)


@router.post("/me/password", status_code=204)
def change_own_password(body: OwnPasswordIn,
                        admin: AdminUser = Depends(require_admin),
                        db: Session = Depends(get_db)):
    if not verify_password(body.current_password, admin.password_hash):
        raise HTTPException(403, "the current password is wrong")
    if body.new_password.lower() == admin.username.lower():
        raise HTTPException(422, "password must differ from the username")
    admin.password_hash = hash_password(body.new_password)
    _bump(admin)
    record(db, _actor(admin), "admin_password_changed",
           {"username": admin.username})
    db.commit()
    return Response(status_code=204)
