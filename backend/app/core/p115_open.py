"""115 开放平台授权与客户端（refresh_token）。

开放平台用长期 refresh_token 代替网页 Cookie：access_token 约 2 小时过期，
刷新时 115 会同时下发新的 refresh_token，旧的立即作废，所以刷新必须串行并
立即落库。用户先在设置页粘贴 refresh_token，后续由后台自动续期。
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import httpx

from ..db import get_setting, now, set_setting
from ..security import decrypt, encrypt
from .p115 import APP_UA, PAGE_LIMIT, P115Client, P115Error, _task_key

PASSPORT = "https://passportapi.115.com"
OPEN_API = "https://proapi.115.com"
TIMEOUT = 30.0
TOKEN_KEY = "p115_open_token"
REFRESH_AHEAD = 300

# 这些错误说明 refresh_token 本身已失效，只能重新授权
REAUTH_ERRNOS = {40140116, 40140119}
# access_token 失效：刷新后重试一次
AUTH_ERRNOS = {40140125}

_lock = threading.Lock()


class P115AuthError(P115Error):
    """需要用户重新粘贴/授权 refresh_token。"""


def _load() -> dict[str, Any]:
    raw = get_setting(TOKEN_KEY, "")
    if not raw:
        return {}
    try:
        data = json.loads(decrypt(str(raw)) or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    set_setting(TOKEN_KEY, encrypt(json.dumps(data, ensure_ascii=False)))


def clear_token() -> None:
    set_setting(TOKEN_KEY, "")


def token_status() -> dict[str, Any]:
    data = _load()
    return {
        "authorized": bool(data.get("refresh_token")),
        "nickname": str(data.get("nickname") or ""),
        "uid": int(data.get("uid") or 0),
        "expires_at": int(data.get("expires_at") or 0),
        "needs_reauth": bool(data.get("needs_reauth")),
        "authorized_at": str(data.get("authorized_at") or ""),
    }


def _refresh_error(payload: dict[str, Any], status: int) -> P115Error:
    errno = payload.get("errno") or payload.get("code")
    try:
        errno_value = int(errno)
    except (TypeError, ValueError):
        errno_value = 0
    message = str(
        payload.get("error")
        or payload.get("message")
        or f"115 授权刷新失败（HTTP {status}）"
    )
    if errno_value in REAUTH_ERRNOS:
        return P115AuthError(
            "115 授权已失效（已过期或已解除授权），请在设置页重新粘贴 refresh_token"
        )
    return P115Error(f"115 授权刷新失败：{message}")


def _request_refresh(refresh_token: str) -> dict[str, Any]:
    try:
        with httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0, write=8.0, pool=5.0),
            follow_redirects=True,
            headers={"User-Agent": APP_UA},
        ) as client:
            response = client.post(
                f"{PASSPORT}/open/refreshToken",
                data={"refresh_token": refresh_token},
            )
    except httpx.HTTPError as exc:
        raise P115Error(f"115 授权刷新请求失败: {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise P115Error(
            f"115 授权刷新响应无法解析（HTTP {response.status_code}）"
        ) from exc
    if not isinstance(payload, dict):
        raise P115Error("115 授权刷新返回格式异常")
    code = payload.get("code")
    failed = (
        response.status_code >= 400
        or payload.get("state") is False
        or bool(payload.get("error"))
        or code not in (0, 200, None)
    )
    if failed:
        raise _refresh_error(payload, response.status_code)
    data = payload.get("data") or {}
    access = str(data.get("access_token") or "")
    refresh = str(data.get("refresh_token") or "")
    if not access or not refresh:
        raise P115Error("115 授权刷新没有返回新的令牌")
    try:
        expires_in = int(data.get("expires_in") or 7200)
    except (TypeError, ValueError):
        expires_in = 7200
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": int(time.time()) + max(120, expires_in - 60),
        "refreshed_at": time.time(),
    }


def _mark_reauth(message: str) -> None:
    data = _load()
    if data.get("refresh_token"):
        data["needs_reauth"] = True
        data["reauth_error"] = message
        _save(data)


def _apply_refresh(data: dict[str, Any], refreshed: dict[str, Any]) -> dict[str, Any]:
    data.update(refreshed)
    data["needs_reauth"] = False
    data.pop("reauth_error", None)
    _save(data)
    return data


def access_token(*, force: bool = False, stale: str = "") -> str:
    """返回可用的 access_token；临近过期或强制时刷新并落库。"""
    data = _load()
    if not data.get("refresh_token"):
        raise P115AuthError("115 开放平台未授权，请在设置页粘贴 refresh_token")
    cached = str(data.get("access_token") or "")
    expires_at = int(data.get("expires_at") or 0)
    if not force and cached and cached != stale and expires_at - REFRESH_AHEAD > time.time():
        return cached
    requested_at = time.time()
    with _lock:
        data = _load()
        cached = str(data.get("access_token") or "")
        expires_at = int(data.get("expires_at") or 0)
        refreshed_at = float(data.get("refreshed_at") or 0)
        fresh = bool(cached) and cached != stale and expires_at > time.time() + 60
        # 其它线程刚刷新过：直接用新 token，避免浪费一次性 refresh_token。
        # 手动强制续期（stale 为空）时仍要真的刷新一次。
        if fresh and (stale or refreshed_at >= requested_at):
            return cached
        refresh_token = str(data.get("refresh_token") or "")
        if not refresh_token:
            raise P115AuthError("115 开放平台未授权，请在设置页粘贴 refresh_token")
        try:
            refreshed = _request_refresh(refresh_token)
        except P115AuthError as exc:
            _mark_reauth(str(exc))
            raise
        data = _apply_refresh(data, refreshed)
        return str(data["access_token"])


def authorize_with_refresh_token(refresh_token: str) -> dict[str, Any]:
    """校验用户粘贴的 refresh_token，立即刷新一次并保存新令牌。"""
    token = (refresh_token or "").strip()
    if not token:
        raise P115Error("请填写 refresh_token")
    # 与后台自动续期串行，避免一次性 refresh_token 被并发消耗
    with _lock:
        refreshed = _request_refresh(token)
        data: dict[str, Any] = {
            "refresh_token": token,
            "nickname": "",
            "authorized_at": now(),
        }
        try:
            user = _user_info(str(refreshed["access_token"]))
        except P115Error:
            user = {}
        if user:
            data["nickname"] = str(user.get("user_name") or user.get("nickname") or "")
            data["uid"] = int(user.get("user_id") or 0)
        return _apply_refresh(data, refreshed)


def refresh_now() -> dict[str, Any]:
    access_token(force=True)
    return token_status()


def _user_info(access: str) -> dict[str, Any]:
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": APP_UA, "Authorization": f"Bearer {access}"},
        ) as client:
            response = client.get(f"{OPEN_API}/open/user/info")
    except httpx.HTTPError as exc:
        raise P115Error(f"115 请求失败: {exc}") from exc
    payload = _parse(response)
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _parse(response: httpx.Response) -> dict[str, Any]:
    if response.status_code == 429:
        raise P115Error("115 请求过于频繁，请稍后再试")
    try:
        payload = response.json()
    except ValueError as exc:
        raise P115Error("115 返回了无法解析的内容") from exc
    if not isinstance(payload, dict):
        raise P115Error("115 返回格式异常")
    if response.status_code >= 500:
        raise P115Error(f"115 HTTP {response.status_code}")
    return payload


def _is_ok(payload: dict[str, Any]) -> bool:
    if payload.get("state") is True or payload.get("state") == 1:
        return True
    if payload.get("state") in (False, 0):
        return False
    return payload.get("code") in (0, 200, None) and not payload.get("error")


def _auth_codes(payload: dict[str, Any]) -> bool:
    for key in ("code", "errno"):
        value = payload.get(key)
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number == 99 or number in AUTH_ERRNOS or str(number).startswith("401"):
            return True
    return False


class P115OpenClient:
    """与 P115Client 接口一致的开放平台客户端。"""

    def __init__(self, *, qps: float = 1.0) -> None:
        self.qps = max(0.2, float(qps or 1.0))
        self._interval = 1.0 / self.qps
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._http = httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": APP_UA},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "P115OpenClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _send(
        self, method: str, url: str, token: str, **kwargs: Any
    ) -> httpx.Response:
        with self._lock:
            wait = self._last_request + self._interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                return self._http.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                raise P115Error(f"115 请求失败: {type(exc).__name__}") from exc
            finally:
                self._last_request = time.monotonic()

    def _request(self, method: str, url: str, retry: bool = True, **kwargs: Any) -> dict[str, Any]:
        token = access_token()
        payload = _parse(self._send(method, url, token, **kwargs))
        if _is_ok(payload):
            return payload
        if retry and _auth_codes(payload):
            token = access_token(force=True, stale=token)
            payload = _parse(self._send(method, url, token, **kwargs))
            if _is_ok(payload):
                return payload
        errno = str(payload.get("errno") or payload.get("code") or "")
        message = str(
            payload.get("error")
            or payload.get("message")
            or payload.get("errmsg")
            or "115 接口返回失败"
        )
        if errno == "10008" or "已存在" in message:
            raise P115Error("115 已存在相同离线任务")
        raise P115Error(f"{message}（errno {errno}）" if errno else message)

    def _data(self, method: str, url: str, **kwargs: Any) -> Any:
        return self._request(method, url, **kwargs).get("data")

    def list_entries(self, folder_id: str = "0") -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._request(
                "GET",
                f"{OPEN_API}/open/ufile/files",
                params={
                    "cid": folder_id or "0",
                    "limit": PAGE_LIMIT,
                    "offset": offset,
                    "show_dir": 1,
                    "fc_mix": 0,
                    "count_folders": 1,
                    "o": "user_ptime",
                    "asc": 1,
                },
            )
            rows = payload.get("data") or []
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                fid = str(row.get("fid") or row.get("cid") or "")
                is_dir = str(row.get("fc") or "") == "0"
                result.append(
                    {
                        "fid": fid,
                        "name": str(row.get("fn") or row.get("n") or row.get("file_name") or ""),
                        "is_dir": is_dir,
                        "size": int(row.get("fs") or row.get("s") or 0),
                        "parent_id": str(row.get("pid") or folder_id or "0"),
                        "sha": str(row.get("sha1") or row.get("sha") or ""),
                    }
                )
            total = int(payload.get("count") or 0)
            offset += len(rows)
            if not rows or offset >= total:
                break
        return result

    def create_folder(self, parent_id: str, name: str) -> str:
        data = self._data(
            "POST",
            f"{OPEN_API}/open/folder/add",
            data={"pid": parent_id or "0", "file_name": name},
        )
        if isinstance(data, dict):
            for key in ("file_id", "cid", "id", "folder_id"):
                value = data.get(key)
                if value:
                    return str(value)
        raise P115Error("115 创建目录成功但没有返回目录 ID")

    def resolve_folder(self, parts: list[str], *, create: bool = False) -> str:
        folder_id = "0"
        for part in parts:
            part = str(part or "").strip()
            if not part:
                continue
            entries = self.list_entries(folder_id)
            match = next(
                (
                    entry
                    for entry in entries
                    if entry["is_dir"] and entry["name"] == part
                ),
                None,
            )
            if match:
                folder_id = str(match["fid"])
            elif create:
                folder_id = self.create_folder(folder_id, part)
            else:
                raise P115Error(f"115 目录不存在: {part}")
        return folder_id

    def ensure_folder_path(self, parts: list[str]) -> str:
        return self.resolve_folder(parts, create=True)

    def list_subfolders(self, parts: list[str]) -> list[dict[str, Any]]:
        folder_id = self.resolve_folder(parts, create=False)
        return [
            {"name": entry["name"], "fid": entry["fid"]}
            for entry in self.list_entries(folder_id)
            if entry["is_dir"]
        ]

    def add_task(self, url: str, *, folder_id: str) -> dict[str, Any]:
        try:
            data = self._data(
                "POST",
                f"{OPEN_API}/open/offline/add_task_urls",
                data={"urls": url, "wp_path_id": folder_id or "0"},
            )
        except P115Error as exc:
            if "已存在" in str(exc):
                existing = self._find_task_by_url(url)
                if existing:
                    return existing
            raise
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        row = rows[0] if rows else {}
        if not isinstance(row, dict) or not row:
            raise P115Error("115 添加离线任务失败：接口没有返回结果")
        message = str(row.get("message") or row.get("error") or "")
        code = row.get("code")
        if row.get("state") is False or code not in (0, "0", None):
            if "已存在" in message:
                existing = self._find_task_by_url(url)
                if existing:
                    return existing
            raise P115Error(message or "115 添加离线任务失败")
        info_hash = str(row.get("info_hash") or "")
        return {
            "id": _task_key(url, info_hash),
            "name": "",
            "status": "waiting",
            "percent": 0.0,
            "url": url,
            "info_hash": info_hash,
        }

    def _find_task_by_url(self, url: str) -> dict[str, Any] | None:
        key = _task_key(url)
        for task in self.list_tasks():
            if task["id"] == key:
                return task
        return None

    def list_tasks(self, max_pages: int = 20) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            data = self._data(
                "GET",
                f"{OPEN_API}/open/offline/get_task_list",
                params={"page": page},
            )
            if not isinstance(data, dict):
                break
            rows = data.get("tasks") or []
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    status_code = int(row.get("status"))
                except (TypeError, ValueError):
                    status_code = 0
                status = {
                    0: "waiting",
                    1: "downloading",
                    2: "done",
                    -1: "failed",
                }.get(status_code, "waiting")
                url = str(row.get("url") or "")
                info_hash = str(row.get("info_hash") or "")
                error = ""
                if status == "failed":
                    error = str(
                        row.get("error_msg")
                        or row.get("error")
                        or row.get("message")
                        or "115 离线任务失败"
                    )
                result.append(
                    {
                        "id": _task_key(url, info_hash),
                        "name": str(row.get("name") or ""),
                        "status": status,
                        "percent": float(row.get("percentDone") or row.get("percent") or 0),
                        "error": error,
                        "url": url,
                        "folder_id": str(row.get("wp_path_id") or ""),
                        "file_id": str(row.get("file_id") or ""),
                        "size": int(row.get("size") or 0),
                    }
                )
            try:
                pages = int(data.get("page_count") or 1)
            except (TypeError, ValueError):
                pages = 1
            if page >= pages:
                break
            page += 1
        return result

    def test(self) -> dict[str, Any]:
        data = self._data("GET", f"{OPEN_API}/open/user/info")
        user = data if isinstance(data, dict) else {}
        return {
            "ok": True,
            "nickname": str(user.get("user_name") or user.get("nickname") or ""),
            "uid": int(user.get("user_id") or 0),
            "checked_at": now(),
            "auth_mode": "open",
        }


def _qps_setting() -> float:
    try:
        qps = float(get_setting("p115_qps", 1) or 1)
    except (TypeError, ValueError):
        qps = 1.0
    return max(0.2, min(qps, 10.0))


def build_p115_client() -> P115Client | P115OpenClient:
    """按设置返回 Cookie 或开放平台客户端。"""
    mode = str(get_setting("p115_auth_mode", "cookie") or "cookie")
    if mode == "open":
        data = _load()
        if not data.get("refresh_token"):
            raise P115AuthError("115 开放平台未授权，请在设置页粘贴 refresh_token")
        if data.get("needs_reauth"):
            raise P115AuthError("115 授权已失效，请在设置页重新粘贴 refresh_token")
        return P115OpenClient(qps=_qps_setting())
    cookie = decrypt(str(get_setting("p115_cookie", "") or ""))
    if not cookie:
        raise P115Error("尚未配置 115 Cookie，请到设置页填写")
    return P115Client(cookie, qps=_qps_setting())
