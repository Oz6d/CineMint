"""115 网盘接口客户端（Cookie 登录）。

目录操作走 webapi.115.com；离线下载走 115.com/web/lixian 的签名接口。
115 的业务错误通常仍返回 HTTP 200，因此所有结果都检查 ``state``。
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Any

import httpx

from ..db import now

WEBAPI = "https://webapi.115.com"
LIXIAN = "https://lixian.115.com"
SITE = "https://115.com"
APP_UA = (
    "Mozilla/5.0 115disk/27.0.5.7 115Browser/27.0.5.7 "
    "115wangpan_android/27.0.5.7"
)
TIMEOUT = 30.0
PAGE_LIMIT = 1150
BTIH_RE = re.compile(r"btih:([0-9a-zA-Z]+)", re.I)


class P115Error(RuntimeError):
    pass


def _cookie_values(cookie: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in (cookie or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip().upper()] = value.strip()
    return values


def _task_key(url: str = "", info_hash: str = "") -> str:
    match = BTIH_RE.search(url or "")
    if match:
        return "btih:" + match.group(1).lower()
    if url:
        return "url:" + hashlib.sha1(url.encode("utf-8")).hexdigest()
    return "hash:" + str(info_hash or "").lower()


class P115Client:
    def __init__(
        self,
        cookie: str,
        *,
        qps: float = 1.0,
        webapi: str = WEBAPI,
        site: str = SITE,
        lixian: str = LIXIAN,
        my: str = "https://my.115.com",
    ) -> None:
        values = _cookie_values(cookie)
        missing = [key for key in ("UID", "CID", "SEID", "KID") if not values.get(key)]
        if missing:
            raise P115Error("115 Cookie 缺少字段：" + "、".join(missing))
        self.cookie = str(cookie).strip()
        self.uid = values["UID"]
        self.qps = max(0.2, float(qps or 1.0))
        self.webapi = webapi.rstrip("/")
        self.site = site.rstrip("/")
        self.lixian = lixian.rstrip("/")
        self.my = my.rstrip("/")
        self._interval = 1.0 / self.qps
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._http = httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers=self._headers(),
        )

    def _headers(self, *, site: bool = False) -> dict[str, str]:
        headers = {"Cookie": self.cookie, "User-Agent": APP_UA}
        if site:
            headers.update(
                {
                    "Referer": "https://115.com/",
                    "Origin": "https://115.com",
                    "X-Requested-With": "XMLHttpRequest",
                }
            )
        return headers

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "P115Client":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _request(
        self, method: str, url: str, *, site: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        with self._lock:
            wait = self._last_request + self._interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                response = self._http.request(
                    method, url, headers=self._headers(site=site), **kwargs
                )
            except httpx.HTTPError as exc:
                raise P115Error(f"115 请求失败: {type(exc).__name__}") from exc
            finally:
                self._last_request = time.monotonic()
        if response.status_code == 429:
            raise P115Error("115 请求过于频繁，请稍后再试")
        if response.status_code >= 400:
            raise P115Error(f"115 HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise P115Error("115 返回了无法解析的内容") from exc
        if not isinstance(data, dict):
            raise P115Error("115 返回格式异常")
        self._raise_for_state(data)
        return data

    def _raise_for_state(self, data: dict[str, Any]) -> None:
        if data.get("state") is True or data.get("state") == 1:
            return
        errno = str(data.get("errno") or data.get("errcode") or "")
        message = str(
            data.get("error")
            or data.get("errmsg")
            or data.get("message")
            or data.get("msg")
            or "115 接口返回失败"
        )
        if errno == "99":
            message = "115 Cookie 已失效，请在网页端重新登录后更新 Cookie"
        elif errno == "911" or "验证" in message:
            message = "115 触发风控验证，请先在网页端完成验证后重试"
        elif errno == "10008" or "已存在" in message:
            message = "115 已存在相同离线任务"
        raise P115Error(f"{message}（errno {errno}）" if errno else message)

    def _get(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self._request("GET", url, **kwargs)

    def _post(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self._request("POST", url, **kwargs)

    def list_entries(self, folder_id: str = "0") -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self._get(
                f"{self.webapi}/files",
                params={
                    "aid": 1,
                    "cid": folder_id or "0",
                    "show_dir": 1,
                    "limit": PAGE_LIMIT,
                    "offset": offset,
                    "o": "user_ptime",
                    "asc": 1,
                    "record_open_time": 1,
                    "format": "json",
                    "fc_mix": 0,
                },
            )
            rows = data.get("data") or []
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                cid = str(row.get("cid") or "")
                fid = str(row.get("fid") or "")
                is_dir = bool(
                    str(row.get("is_dir") or "").lower() in {"1", "true"}
                    or (not fid and cid and cid != "0")
                )
                entry_id = (cid if is_dir else fid) or cid or fid
                result.append(
                    {
                        "fid": entry_id,
                        "name": str(row.get("n") or row.get("file_name") or ""),
                        "is_dir": is_dir,
                        "size": int(row.get("s") or row.get("size") or 0),
                        "parent_id": str(row.get("pid") or folder_id or "0"),
                        "sha": str(row.get("sha") or ""),
                    }
                )
            total = int(data.get("count") or 0)
            offset += len(rows)
            if not rows or offset >= total:
                break
        return result

    def create_folder(self, parent_id: str, name: str) -> str:
        data = self._post(
            f"{self.webapi}/files/add",
            data={"pid": parent_id or "0", "cname": name},
        )
        for key in ("cid", "id", "folder_id", "file_id"):
            value = data.get(key)
            if value:
                return str(value)
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("cid", "id", "folder_id", "file_id"):
                value = nested.get(key)
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
        space = self._get(
            f"{self.site}/?ct=offline&ac=space&_={int(time.time() * 1000)}",
            site=True,
        )
        sign = str(space.get("sign") or "")
        sign_time = str(space.get("time") or "")
        if not sign:
            raise P115Error("115 没有返回离线下载签名")
        data = self._post(
            f"{self.site}/web/lixian/?ct=lixian&ac=add_task_urls",
            site=True,
            data={
                "url[0]": url,
                "savepath": "",
                "wp_path_id": folder_id or "0",
                "uid": self.uid,
                "sign": sign,
                "time": sign_time,
            },
        )
        rows = data.get("result") or []
        if isinstance(rows, dict):
            rows = [rows]
        row = rows[0] if isinstance(rows, list) and rows else {}
        if not isinstance(row, dict) or not row:
            raise P115Error("115 添加离线任务失败：接口没有返回结果")
        if row.get("state") is False or row.get("error_msg"):
            message = str(row.get("error_msg") or row.get("error") or "115 添加离线任务失败")
            if "已存在" in message:
                existing = self._find_task_by_url(url)
                if existing:
                    return existing
            raise P115Error(message)
        info_hash = str(row.get("info_hash") or "") if isinstance(row, dict) else ""
        return {
            "id": _task_key(url, info_hash),
            "name": str(row.get("name") or "") if isinstance(row, dict) else "",
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
            data = self._get(
                f"{self.lixian}/web/lixian/",
                params={"ct": "lixian", "ac": "task_lists", "page": page},
            )
            rows = data.get("tasks") or []
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                raw_status = row.get("status")
                try:
                    status_code = int(raw_status)
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
                        or "115 离线任务失败"
                    )
                elif status == "done" and str(row.get("move")) == "-1":
                    error = "115 下载完成，但移动到保存目录失败"
                    status = "failed"
                result.append(
                    {
                        "id": _task_key(url, info_hash),
                        "name": str(row.get("name") or ""),
                        "status": status,
                        "percent": float(row.get("percentDone") or 0),
                        "error": error,
                        "url": url,
                        "folder_id": str(row.get("wp_path_id") or ""),
                        "file_id": str(row.get("file_id") or ""),
                        "size": int(row.get("size") or 0),
                    }
                )
            pages = int(data.get("page_count") or 1)
            if page >= pages:
                break
            page += 1
        return result

    def test(self) -> dict[str, Any]:
        data = self._get(
            f"{self.my}/?ct=guide&ac=status",
            site=True,
        )
        nickname = str(data.get("nickname") or data.get("user_name") or "")
        return {"ok": True, "nickname": nickname, "checked_at": now()}
