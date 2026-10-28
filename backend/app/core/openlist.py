"""OpenList / AList 目录缓存刷新。

云盘转存完成后，OpenList 仍可能返回旧目录缓存，WebDAV 侧就会看不到新文件。
这里直接调用 OpenList 的 HTTP API 登录并对目标目录发起 ``fs/list`` +
``refresh``，让它在扫描前回源刷新；失败只影响同步速度，不影响转存本身。
"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from .providers import decrypt_config

TOKEN_TTL = 30 * 60
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=5.0, write=8.0, pool=5.0)
# OpenList v4 只接受裸 token，AList 兼容 Bearer 前缀，按顺序兼容
AUTH_SCHEMES = ("", "Bearer ")

_lock = threading.Lock()
_tokens: dict[tuple[str, str, bool], tuple[str, float]] = {}


class OpenListError(RuntimeError):
    pass


class NotOpenList(OpenListError):
    """目标不是 OpenList/AList，直接跳过刷新。"""


def _api_target(source_row: dict[str, Any] | None) -> tuple[str, dict[str, Any]] | None:
    """从 WebDAV 文件源推导 OpenList API 根地址；非 OpenList 返回 None。"""
    if not source_row or source_row.get("type") != "webdav":
        return None
    config = decrypt_config(source_row.get("config"))
    raw_url = str(config.get("url") or "").strip()
    if not raw_url:
        return None
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    dav_path = unquote(parsed.path).rstrip("/")
    if dav_path == "":
        base_path = ""
    elif dav_path.endswith("/dav"):
        base_path = dav_path[: -len("/dav")].rstrip("/")
    else:
        return None
    return f"{parsed.scheme}://{parsed.netloc}{base_path}", config


def _login(client: httpx.Client, base: str, username: str, password: str) -> str:
    try:
        response = client.post(
            f"{base}/api/auth/login",
            json={"username": username, "password": password},
        )
    except httpx.HTTPError as exc:
        raise OpenListError(f"连接失败: {exc}") from exc
    if response.status_code == 404:
        raise NotOpenList("不是 OpenList/AList 地址（登录接口不存在）")
    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenListError(f"登录响应无法解析（HTTP {response.status_code}）") from exc
    code = payload.get("code")
    message = str(payload.get("message") or "")
    if response.status_code == 401 or code == 401:
        raise OpenListError("用户名或密码错误")
    if code not in (200, None) or response.status_code >= 400:
        raise OpenListError(message or f"登录失败（HTTP {response.status_code}）")
    token = str((payload.get("data") or {}).get("token") or "")
    if not token:
        raise OpenListError("登录成功但没有返回 token")
    return token


def _token(
    client: httpx.Client,
    base: str,
    username: str,
    password: str,
    verify_tls: bool,
) -> str:
    key = (base, username, verify_tls)
    with _lock:
        cached = _tokens.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
    token = _login(client, base, username, password)
    with _lock:
        _tokens[key] = (token, time.monotonic() + TOKEN_TTL)
    return token


def _drop_token(base: str, username: str, verify_tls: bool) -> None:
    with _lock:
        _tokens.pop((base, username, verify_tls), None)


def _request_refresh(
    client: httpx.Client, url: str, token: str, path: str, scheme: str = ""
) -> dict[str, Any] | None:
    """按指定 Authorization 头发起刷新；token 失效返回 None。

    OpenList v4.x 只认裸 token，较老的 AList 兼容 ``Bearer `` 前缀，
    所以由调用方依次尝试。失效时 OpenList 仍回 HTTP 200 + ``code: 401``。
    """
    try:
        response = client.post(
            url,
            json={"path": path, "password": "", "page": 1, "per_page": 1, "refresh": True},
            headers={"Authorization": f"{scheme}{token}"},
        )
    except httpx.HTTPError as exc:
        raise OpenListError(f"刷新请求失败: {exc}") from exc
    if response.status_code == 401:
        return None
    try:
        payload = response.json()
    except ValueError as exc:
        raise OpenListError(
            f"刷新响应无法解析（HTTP {response.status_code}）"
        ) from exc
    code = payload.get("code")
    if code == 401:
        return None
    if response.status_code >= 400 or code not in (200, None):
        raise OpenListError(
            str(payload.get("message") or f"刷新失败（HTTP {response.status_code}）")
        )
    return payload


def refresh_directory(
    source_row: dict[str, Any] | None, path: str
) -> bool:
    """强制 OpenList 回源刷新目录缓存。

    返回 True 表示已刷新；False 表示该文件源不是 OpenList/WebDAV。
    网络、认证或权限问题抛出 OpenListError，调用方按警告处理即可。
    """
    target = _api_target(source_row)
    if target is None:
        return False
    base, config = target
    username = str(config.get("username") or "")
    password = str(config.get("password") or "")
    if not username or not password:
        raise OpenListError("文件源没有填写用户名或密码，无法自动刷新目录")
    verify_tls = bool(config.get("verify_tls", True))
    api_path = "/" + str(path or "").strip().lstrip("/")
    if api_path == "/":
        raise OpenListError("目录为空，无法刷新")
    url = f"{base}/api/fs/list"
    with httpx.Client(
        timeout=REQUEST_TIMEOUT,
        verify=verify_tls,
        follow_redirects=True,
        headers={"User-Agent": "CineMint/0.1"},
    ) as client:
        try:
            token = _token(client, base, username, password, verify_tls)
        except NotOpenList:
            return False
        for scheme in AUTH_SCHEMES:
            if _request_refresh(client, url, token, api_path, scheme) is not None:
                return True
        _drop_token(base, username, verify_tls)
        token = _token(client, base, username, password, verify_tls)
        for scheme in AUTH_SCHEMES:
            if _request_refresh(client, url, token, api_path, scheme) is not None:
                return True
    raise OpenListError("登录状态已失效，无法刷新目录")
