from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..config import VERSION, settings
from ..db import get_setting, set_setting
from ..security import (
    create_session_token,
    hash_password,
    verify_password,
)
from .deps import COOKIE_NAME, client_ip, require_auth
from fastapi import Depends

router = APIRouter(prefix="/api/auth", tags=["auth"])

_attempts: dict[str, list[float]] = defaultdict(list)


class LoginBody(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response):
    ip = client_ip(request)
    now_ts = time.time()
    recent = [stamp for stamp in _attempts[ip] if now_ts - stamp < 60]
    _attempts[ip] = recent
    if len(recent) >= 10:
        raise HTTPException(status_code=429, detail="尝试次数过多，请一分钟后再试")

    encoded = get_setting("admin_password_hash", "")
    ok = verify_password(body.password, encoded) if encoded else body.password == settings.admin_password
    if not ok:
        _attempts[ip].append(now_ts)
        raise HTTPException(status_code=401, detail="密码错误")

    token = create_session_token()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    _attempts[ip].clear()
    return {"ok": True, "version": VERSION}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(_: bool = Depends(require_auth)):
    return {
        "authenticated": True,
        "version": VERSION,
        "password_is_default": not bool(get_setting("password_changed", False)),
        "demo": bool(get_setting("demo_seeded", False)),
    }


class PasswordBody(BaseModel):
    current: str
    new: str


@router.post("/password")
def change_password(body: PasswordBody, _: bool = Depends(require_auth)):
    if len(body.new) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    encoded = get_setting("admin_password_hash", "")
    valid = verify_password(body.current, encoded) if encoded else body.current == settings.admin_password
    if not valid:
        raise HTTPException(status_code=400, detail="当前密码不正确")
    set_setting("admin_password_hash", hash_password(body.new))
    set_setting("password_changed", True)
    return {"ok": True}
