from __future__ import annotations

from fastapi import Cookie, HTTPException, Request

from ..security import verify_session_token

COOKIE_NAME = "cinemint_session"


def require_auth(request: Request, cinemint_session: str | None = Cookie(default=None)) -> bool:
    token = cinemint_session or request.cookies.get(COOKIE_NAME)
    if not token or not verify_session_token(token):
        raise HTTPException(status_code=401, detail="请先登录")
    return True


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
