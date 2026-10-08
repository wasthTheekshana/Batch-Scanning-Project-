"""Admin login/logout (session cookie + CSRF token)."""
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from central.deps import get_db, require_admin, require_csrf
from central.models import AdminUser, utcnow
from central.security import burn_password_check, verify_password

router = APIRouter(prefix="/admin", tags=["admin-auth"])


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
                        lockout_minutes: int) -> None:
    """Atomically record a failed login attempt.

    Uses UPDATE statement to avoid read-modify-write race conditions.
    Increments failed_count, and if threshold reached, sets locked_until.
    Does NOT increment session_version; session invalidation is only done at logout."""
    now = utcnow()
    # Atomic increment of failed_count
    db.execute(update(AdminUser).where(AdminUser.id == user_id).values(
        failed_count=AdminUser.failed_count + 1))
    db.commit()

    # Re-read to check if we've hit the threshold
    user = db.get(AdminUser, user_id)
    if user.failed_count >= max_failures:
        # Lockout: reset failed_count and set locked_until
        # (session_version NOT incremented to prevent forced logout)
        db.execute(update(AdminUser).where(AdminUser.id == user_id).values(
            failed_count=0,
            locked_until=now + timedelta(minutes=lockout_minutes)))
        db.commit()


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
        raise HTTPException(401, "invalid credentials")

    ok = (verify_password(body.password, user.password_hash) if user
          else burn_password_check(body.password))
    if not ok:
        if user:
            record_failed_login(db, user.id, s.login_max_failures,
                              s.login_lockout_minutes)
        raise HTTPException(401, "invalid credentials")

    # Successful login: reset failure counters and create session
    db.execute(update(AdminUser).where(AdminUser.id == user.id).values(
        failed_count=0, locked_until=None))
    db.commit()

    request.session.clear()  # new session on every login (no fixation)
    request.session["admin_id"] = user.id
    request.session["sv"] = user.session_version
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"username": user.username, "role": user.role,
            "csrf_token": request.session["csrf"]}


@router.get("/me")
def me(user: AdminUser = Depends(require_admin)):
    return {"username": user.username, "role": user.role}


@router.post("/logout", status_code=204,
             dependencies=[Depends(require_admin), Depends(require_csrf)])
def logout(request: Request, db: Session = Depends(get_db)):
    admin_id = request.session.get("admin_id")
    # Increment session_version to invalidate all existing sessions for this user
    db.execute(update(AdminUser).where(AdminUser.id == admin_id).values(
        session_version=AdminUser.session_version + 1))
    db.commit()
    request.session.clear()
    return Response(status_code=204)
