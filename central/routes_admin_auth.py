"""Admin login/logout (session cookie + CSRF token)."""
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

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


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    s = request.app.state.settings
    now = utcnow()
    user = db.scalar(select(AdminUser).where(
        AdminUser.username == body.username))
    if user and user.locked_until and user.locked_until > now:
        raise HTTPException(429, "too many failed attempts; try again later")
    ok = (verify_password(body.password, user.password_hash) if user
          else burn_password_check(body.password))
    if not ok:
        if user:
            user.failed_count += 1
            if user.failed_count >= s.login_max_failures:
                user.locked_until = now + timedelta(
                    minutes=s.login_lockout_minutes)
                user.failed_count = 0
            db.commit()
        raise HTTPException(401, "invalid credentials")
    user.failed_count = 0
    user.locked_until = None
    db.commit()
    request.session.clear()  # new session on every login (no fixation)
    request.session["admin_id"] = user.id
    request.session["csrf"] = secrets.token_urlsafe(32)
    return {"username": user.username, "role": user.role,
            "csrf_token": request.session["csrf"]}


@router.get("/me")
def me(user: AdminUser = Depends(require_admin)):
    return {"username": user.username, "role": user.role}


@router.post("/logout", status_code=204,
             dependencies=[Depends(require_admin), Depends(require_csrf)])
def logout(request: Request):
    request.session.clear()
    return Response(status_code=204)
