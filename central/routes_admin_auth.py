"""Admin login/logout (session cookie + CSRF token)."""
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from central.audit import record
from central.deps import get_db, require_admin, require_csrf
from central.models import AdminUser, utcnow
from central.security import burn_password_check, verify_password

router = APIRouter(prefix="/admin", tags=["admin-auth"])
log = logging.getLogger("dokscanner.central")


class LoginIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=False)
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("username", "password", mode="before")
    @classmethod
    def validate_utf8(cls, value):
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError("username and password must be valid UTF-8")
        return value


def record_failed_login(db: Session, user_id: int, max_failures: int,
                        lockout_minutes: int) -> bool:
    """Atomically record a failed login attempt.

    Uses UPDATE statement to avoid read-modify-write race conditions.
    Increments failed_count, and if threshold reached, sets locked_until.
    Returns True when this failure tripped the lockout.
    Does NOT increment session_version; session invalidation is only done at logout."""
    now = utcnow()
    # Increment and read in ONE statement: the lock decision must use the
    # value the database produced, never a cached ORM object.
    new_count = db.execute(
        update(AdminUser).where(AdminUser.id == user_id)
        .values(failed_count=AdminUser.failed_count + 1)
        .returning(AdminUser.failed_count)
        .execution_options(synchronize_session=False)).scalar_one()
    locked = new_count >= max_failures
    if locked:
        # Lockout: reset failed_count and set locked_until
        # (session_version NOT incremented to prevent forced logout)
        db.execute(
            update(AdminUser).where(AdminUser.id == user_id)
            .values(failed_count=0,
                    locked_until=now + timedelta(minutes=lockout_minutes))
            .execution_options(synchronize_session=False))
    db.commit()
    return locked


def _audit_failure(db: Session, username: str, reason: str) -> None:
    """username is only ever passed for an existing account."""
    record(db, f"admin:{username}", "admin_login_failed",
           {"username": username, "reason": reason})
    db.commit()
    log.warning("admin login failed: %s (%s)", username, reason)


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    s = request.app.state.settings
    now = utcnow()
    user = db.scalar(select(AdminUser).where(
        AdminUser.username == body.username))

    # Check if user is locked
    if user and user.locked_until and user.locked_until > now:
        # Locked user looks identical to unknown user (same 401 response)
        burn_password_check(body.password)
        _audit_failure(db, user.username, "locked_account")
        raise HTTPException(401, "invalid credentials")

    ok = (verify_password(body.password, user.password_hash) if user
          else burn_password_check(body.password))
    if not ok:
        if user:
            locked = record_failed_login(db, user.id, s.login_max_failures,
                                         s.login_lockout_minutes)
            _audit_failure(db, user.username, "bad_password")
            if locked:
                record(db, f"admin:{user.username}", "admin_locked",
                       {"username": user.username,
                        "minutes": s.login_lockout_minutes})
                db.commit()
                log.warning("admin account locked: %s", user.username)
        else:
            # Unknown username: nothing identifying is stored or logged.
            record(db, "anonymous", "admin_login_failed", {})
            db.commit()
            log.warning("admin login failed: unknown user")
        raise HTTPException(401, "invalid credentials")

    # Successful login: reset failure counters and create session
    db.execute(update(AdminUser).where(AdminUser.id == user.id).values(
        failed_count=0, locked_until=None))
    db.commit()

    record(db, f"admin:{user.username}", "admin_login",
           {"username": user.username})
    db.commit()
    log.info("admin login: %s", user.username)

    request.session.clear()  # new session on every login (no fixation)
    request.session["admin_id"] = user.id
    request.session["sv"] = user.session_version
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"username": user.username, "role": user.role,
            "csrf_token": request.session["csrf"]}


@router.get("/me")
def me(request: Request, user: AdminUser = Depends(require_admin)):
    # the token lets a reloaded page (same session cookie, fresh JS) keep
    # making changes; it is only readable same-origin (no CORS is enabled)
    return {"username": user.username, "role": user.role,
            "csrf_token": request.session.get("csrf")}


@router.post("/logout", status_code=204,
             dependencies=[Depends(require_admin), Depends(require_csrf)])
def logout(request: Request, admin: AdminUser = Depends(require_admin),
           db: Session = Depends(get_db)):
    admin_id = request.session.get("admin_id")
    username = admin.username
    # Increment session_version to invalidate all existing sessions for this user
    db.execute(update(AdminUser).where(AdminUser.id == admin_id).values(
        session_version=AdminUser.session_version + 1))
    record(db, f"admin:{username}", "admin_logout", {"username": username})
    db.commit()
    log.info("admin logout: %s", username)
    request.session.clear()
    return Response(status_code=204)
