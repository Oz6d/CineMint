"""夸克网盘开放接口的最小客户端。

只实现订阅需要的部分：分享链接解析、分享目录遍历、转存任务、
网盘目录创建 / 列表 / 删除。所有请求串行并做 QPS 限速。
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .cloud_paths import default_save_subdir, safe_save_subdir, sanitize_component, staging_paths
from .providers.base import norm

QUARK_API_BASE = "https://drive-pc.quark.cn/1/clouddrive"
SHARE_URL_RE = re.compile(
    r"(?:pan|drive)\.quark\.cn/s/([A-Za-z0-9_-]{6,})", re.I
)
SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class QuarkError(RuntimeError):
    pass


class QuarkAuthError(QuarkError):
    pass


class QuarkShareError(QuarkError):
    pass


class QuarkShareCancelledError(QuarkShareError):
    """夸克分享已被取消、删除或失效，需要用户更新分享链接。"""


_CANCELLED_SHARE_HINTS = (
    "取消",
    "失效",
    "过期",
    "不存在",
    "已删除",
    "被删除",
    "屏蔽",
    "违规",
    "不可用",
)


def is_share_cancelled_message(message: str) -> bool:
    text = str(message or "").strip()
    if not text or "提取码" in text or "密码" in text:
        return False
    return any(hint in text for hint in _CANCELLED_SHARE_HINTS)


@dataclass
class ShareLink:
    share_id: str
    passcode: str = ""


@dataclass
class ShareEntry:
    fid: str
    token: str
    name: str
    is_dir: bool
    size: int = 0
    path: str = ""


def parse_share_link(value: str, passcode: str = "") -> ShareLink:
    """解析夸克分享链接或裸 share_id，并从链接参数中提取提取码。"""
    raw = (value or "").strip()
    if not raw:
        raise QuarkShareError("请填写夸克分享链接")
    match = SHARE_URL_RE.search(raw)
    if match:
        share_id = match.group(1)
    elif SHARE_ID_RE.fullmatch(raw):
        share_id = raw
    else:
        raise QuarkShareError("无法识别夸克分享链接，请使用 https://pan.quark.cn/s/… 格式")
    found = passcode.strip()
    parsed = urlparse(raw)
    for query in (parsed.query, parsed.fragment.partition("?")[2]):
        if not query:
            continue
        params = parse_qs(query)
        for key in ("pwd", "password", "passcode"):
            values = params.get(key) or []
            if values and not found:
                found = unquote(values[0]).strip()
    return ShareLink(share_id=share_id, passcode=found[:16])


class QuarkClient:
    def __init__(
        self,
        cookie: str,
        *,
        base_url: str = QUARK_API_BASE,
        qps: float = 2.0,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        cookie = (cookie or "").strip()
        if cookie.lower().startswith("cookie:"):
            cookie = cookie[7:].strip()
        if not cookie:
            raise QuarkAuthError("请先在设置中填写夸克 Cookie")
        self.cookie = cookie
        self.base_url = base_url.rstrip("/")
        self._min_interval = 1.0 / max(0.1, float(qps or 0)) if qps else 0.0
        self._last_request = 0.0
        self._throttle_lock = threading.Lock()
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://pan.quark.cn/",
                "Origin": "https://pan.quark.cn",
                "Cookie": cookie,
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "QuarkClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        with self._throttle_lock:
            now = time.monotonic()
            wait = self._last_request + self._min_interval - now
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        query: dict[str, Any] = {"pr": "ucpro", "fr": "pc", **(params or {})}
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(3):
            self._throttle()
            try:
                response = self._client.request(
                    method, url, params=query, json=json_body
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise QuarkError(f"夸克接口连接失败: {exc}") from exc
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            if response.status_code in (401, 403):
                raise QuarkAuthError("夸克 Cookie 已失效，请在设置中更新")
            try:
                payload = response.json()
            except ValueError as exc:
                raise QuarkError(
                    f"夸克接口返回异常（HTTP {response.status_code}）"
                ) from exc
            code = payload.get("code", 0)
            if response.status_code >= 400 or code not in (0, 200):
                message = str(
                    payload.get("message")
                    or payload.get("msg")
                    or f"夸克接口错误 {code}"
                )
                if path.startswith("share/sharepage/") and is_share_cancelled_message(message):
                    raise QuarkShareCancelledError(message)
                if code in {31001, 31002, 31003, 31004}:
                    if is_share_cancelled_message(message):
                        raise QuarkShareCancelledError(message)
                    raise QuarkShareError(message)
                if response.status_code in (401, 403) or code in {401, 403}:
                    raise QuarkAuthError("夸克 Cookie 已失效，请在设置中更新")
                raise QuarkError(message)
            return payload.get("data") or {}
        raise QuarkError(f"夸克接口连接失败: {last_error}")

    # ---------- 账号 ----------
    def account_info(self) -> dict[str, Any]:
        # drive-pc 的 /account/info 已下线（固定 404），/member 仍会校验登录态。
        try:
            return self._request("GET", "/member") or {}
        except QuarkAuthError:
            raise
        except QuarkError as exc:
            try:
                return self._request("GET", "/account/info") or {}
            except QuarkAuthError:
                raise
            except QuarkError:
                raise exc

    def _web_nickname(self) -> str:
        if self.base_url != QUARK_API_BASE:
            return ""
        try:
            response = self._client.get(
                "https://pan.quark.cn/account/info", params={"fr": "pc"}
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return ""
        data = payload.get("data") if isinstance(payload, dict) else None
        return str((data or {}).get("nickname") or "")

    def test(self) -> dict[str, Any]:
        info = self.account_info()
        nickname = (
            info.get("nickname")
            or info.get("nick_name")
            or info.get("member_nickname")
            or self._web_nickname()
            or info.get("member_type")
            or "夸克账号"
        )
        return {"ok": True, "nickname": str(nickname)}

    # ---------- 分享 ----------
    def share_token(self, share_id: str, passcode: str = "") -> str:
        data = self._request(
            "POST",
            "/share/sharepage/token",
            json_body={"pwd_id": share_id, "passcode": passcode or ""},
        )
        token = str((data or {}).get("stoken") or "")
        if not token:
            raise QuarkShareError("获取分享访问令牌失败，请检查提取码")
        return token

    def _normalize_share_item(self, item: dict[str, Any], parent: str = "") -> ShareEntry:
        name = str(item.get("file_name") or item.get("name") or "")
        fid = str(item.get("fid") or item.get("file_id") or "")
        token = str(item.get("share_fid_token") or item.get("fid_token") or "")
        return ShareEntry(
            fid=fid,
            token=token,
            name=name,
            is_dir=bool(item.get("dir")),
            size=int(item.get("size") or 0),
            path=norm("/".join(part for part in (parent, name) if part)),
        )

    def list_share(
        self,
        share_id: str,
        stoken: str,
        pdir_fid: str = "0",
        *,
        page_size: int = 200,
    ) -> list[ShareEntry]:
        entries: list[ShareEntry] = []
        page = 1
        total = 0
        while page <= 100:
            data = self._request(
                "GET",
                "/share/sharepage/detail",
                params={
                    "pwd_id": share_id,
                    "stoken": stoken,
                    "pdir_fid": pdir_fid or "0",
                    "force": 0,
                    "_page": page,
                    "_size": page_size,
                    "_fetch_total": 1,
                    "_sort": "file_type:asc,file_name:asc",
                },
            ) or {}
            raw_items = data.get("list") or []
            metadata = data.get("metadata") or {}
            total = int(metadata.get("_total") or total or 0)
            entries.extend(self._normalize_share_item(item) for item in raw_items)
            if not raw_items or len(raw_items) < page_size:
                break
            if total and len(entries) >= total:
                break
            page += 1
        return entries

    def find_share_dir(
        self, share_id: str, stoken: str, path: str
    ) -> ShareEntry | None:
        current = ShareEntry(fid="0", token="", name="", is_dir=True, path="")
        for part in [item for item in norm(path).split("/") if item]:
            children = self.list_share(share_id, stoken, current.fid)
            match = next(
                (entry for entry in children if entry.is_dir and entry.name == part),
                None,
            )
            if match is None:
                match = next(
                    (
                        entry
                        for entry in children
                        if entry.is_dir and entry.name.lower() == part.lower()
                    ),
                    None,
                )
            if match is None:
                return None
            match.path = norm("/".join(part for part in (current.path, match.name) if part))
            current = match
        return current

    def find_share_fid(
        self,
        share_id: str,
        stoken: str,
        fid: str,
        *,
        max_depth: int = 8,
        max_entries: int = 20000,
    ) -> ShareEntry | None:
        """按 fid 在分享内查找目录（含 path），用于解析分享链接 #/list/share/<fid>。"""
        fid = str(fid or "").strip()
        root = ShareEntry(fid="0", token="", name="", is_dir=True, path="")
        if not fid or fid == "0":
            return root
        visited: set[str] = set()
        queue: list[tuple[ShareEntry, int]] = [(root, 0)]
        seen = 0
        while queue:
            folder, depth = queue.pop(0)
            if folder.fid in visited:
                continue
            visited.add(folder.fid)
            children = self.list_share(share_id, stoken, folder.fid)
            seen += len(children)
            for child in children:
                child.path = norm(
                    "/".join(part for part in (folder.path, child.name) if part)
                )
                if child.fid == fid:
                    return child if child.is_dir else None
                if (
                    child.is_dir
                    and depth < max_depth
                    and not child.name.startswith(".")
                ):
                    queue.append((child, depth + 1))
            if seen >= max_entries:
                return None
        return None

    def walk_share(
        self,
        share_id: str,
        stoken: str,
        *,
        share_subdir: str = "",
        max_depth: int = 8,
    ) -> list[ShareEntry]:
        start = self.find_share_dir(share_id, stoken, share_subdir)
        if start is None:
            raise QuarkShareError(f"分享中找不到目录: {share_subdir}")
        files: list[ShareEntry] = []
        queue: list[tuple[ShareEntry, int]] = [(start, 0)]
        while queue:
            folder, depth = queue.pop(0)
            children = self.list_share(share_id, stoken, folder.fid)
            for child in children:
                child.path = norm(
                    "/".join(part for part in (folder.path, child.name) if part)
                )
                if child.is_dir:
                    if depth < max_depth and not child.name.startswith("."):
                        queue.append((child, depth + 1))
                    continue
                if child.token or child.fid:
                    files.append(child)
        return files

    def save_share_files(
        self,
        share_id: str,
        stoken: str,
        *,
        fids: list[str],
        tokens: list[str],
        to_pdir_fid: str,
        pdir_fid: str = "0",
    ) -> dict[str, Any]:
        if not fids:
            return {"task_id": "", "fids": []}
        data = self._request(
            "POST",
            "/share/sharepage/save",
            json_body={
                "fid_list": fids,
                "fid_token_list": tokens,
                "to_pdir_fid": to_pdir_fid,
                "pwd_id": share_id,
                "stoken": stoken,
                "pdir_fid": pdir_fid or "0",
                "scene": "link",
            },
        ) or {}
        save_as = data.get("save_as") or {}
        return {
            "task_id": str(data.get("task_id") or ""),
            "fids": [
                str(fid)
                for fid in (save_as.get("save_as_top_fids") or [])
                if fid
            ],
        }

    def wait_task(self, task_id: str, *, timeout: float = 90.0) -> dict[str, Any]:
        if not task_id:
            return {}
        deadline = time.monotonic() + max(5.0, timeout)
        while time.monotonic() < deadline:
            data = self._request(
                "GET",
                "/task",
                params={"task_id": task_id, "retry_index": 0},
            ) or {}
            try:
                status = int(data.get("status", -1))
            except (TypeError, ValueError):
                status = -1
            if status == 2:
                return data
            if status in (3, 4):
                raise QuarkError(str(data.get("message") or "夸克转存任务失败"))
            time.sleep(1.0)
        raise QuarkError("夸克转存任务超时，请稍后在网盘确认")

    # ---------- 网盘目录 ----------
    def _normalize_file_item(self, item: dict[str, Any]) -> ShareEntry:
        return ShareEntry(
            fid=str(item.get("fid") or ""),
            token="",
            name=str(item.get("file_name") or item.get("name") or ""),
            is_dir=bool(item.get("dir")),
            size=int(item.get("size") or 0),
            path=str(item.get("file_name") or item.get("name") or ""),
        )

    def list_folder(
        self, pdir_fid: str = "0", *, page_size: int = 200
    ) -> list[ShareEntry]:
        entries: list[ShareEntry] = []
        page = 1
        while page <= 100:
            data = self._request(
                "GET",
                "/file/sort",
                params={
                    "pdir_fid": pdir_fid or "0",
                    "_page": page,
                    "_size": page_size,
                    "_fetch_total": 1,
                    "_sort": "file_type:asc,file_name:asc",
                },
            ) or {}
            raw_items = data.get("list") or []
            entries.extend(self._normalize_file_item(item) for item in raw_items)
            if not raw_items or len(raw_items) < page_size:
                break
            page += 1
        return entries

    def create_folder(self, parent_fid: str, name: str) -> str:
        data = self._request(
            "POST",
            "/file",
            json_body={
                "pdir_fid": parent_fid or "0",
                "file_name": name,
                "dir_path": "",
                "dir_init_lock": False,
            },
        ) or {}
        fid = str(data.get("fid") or "")
        if fid:
            return fid
        for entry in self.list_folder(parent_fid):
            if entry.is_dir and entry.name == name:
                return entry.fid
        raise QuarkError(f"创建夸克目录失败: {name}")

    def ensure_folder_path(self, parts: list[str]) -> str:
        fid = "0"
        for part in parts:
            if not part:
                continue
            existing = next(
                (
                    entry
                    for entry in self.list_folder(fid)
                    if entry.is_dir and entry.name == part
                ),
                None,
            )
            fid = existing.fid if existing else self.create_folder(fid, part)
        return fid

    def existing_folder_path(self, parts: list[str]) -> str:
        """只读定位，不创建目录；不存在时抛出 QuarkError。"""
        fid = "0"
        for part in parts:
            if not part:
                continue
            match = next(
                (
                    entry
                    for entry in self.list_folder(fid)
                    if entry.is_dir and entry.name == part
                ),
                None,
            )
            if match is None:
                raise QuarkError(f"夸克目录不存在: {'/'.join(parts)}")
            fid = match.fid
        return fid

    def list_subfolders(self, parts: list[str]) -> list[dict[str, Any]]:
        fid = self.existing_folder_path(parts)
        return [
            {"fid": entry.fid, "name": entry.name}
            for entry in self.list_folder(fid)
            if entry.is_dir
        ]

    def delete_files(self, fids: list[str]) -> None:
        fids = [fid for fid in fids if fid]
        if not fids:
            return
        self._request(
            "POST",
            "/file/delete",
            json_body={
                "action_type": 2,
                "filelist": fids,
                "exclude_fids": [],
            },
        )
