"""115 开放平台接口本地假服务，供授权与客户端协议测试使用。"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

BTIH_RE = re.compile(r"btih:([0-9a-zA-Z]+)", re.I)

AUTH_ERROR = {"state": False, "code": 40140125, "message": "access token invalid"}


def task_key(url: str) -> str:
    match = BTIH_RE.search(url or "")
    if match:
        return "btih:" + match.group(1).lower()
    return "url:" + hashlib.sha1(url.encode()).hexdigest()


class MockOpenState:
    def __init__(self) -> None:
        self.refresh_tokens = {"initial-token"}
        self.access_tokens: set[str] = set()
        self.counter = 0
        self.refresh_count = 0
        self.refresh_mode = ""
        self.expire_once = False
        self.fail_99_once = False
        self.refresh_delay = 0.0
        self.entries: dict[str, list[dict[str, Any]]] = {"0": []}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.duplicate = False
        self.added_fields: list[dict[str, list[str]]] = []
        self._lock = threading.Lock()

    def issue(self) -> tuple[str, str]:
        with self._lock:
            self.counter += 1
            return f"access-{self.counter}", f"refresh-{self.counter}"

    def add_folder(self, folder_id: str, name: str, parent: str = "0") -> str:
        self.entries.setdefault(parent, []).append(
            {"fid": folder_id, "fn": name, "pc": "", "fc": "0", "fs": 0, "pid": parent}
        )
        self.entries.setdefault(folder_id, [])
        return folder_id

    def add_file(self, fid: str, name: str, parent: str = "0", size: int = 1024) -> str:
        self.entries.setdefault(parent, []).append(
            {"fid": fid, "fn": name, "pc": "", "fc": "1", "fs": size, "pid": parent}
        )
        return fid


def _json(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _ok(data: Any) -> dict[str, Any]:
    return {"state": True, "code": 0, "data": data}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        return

    @property
    def state(self) -> MockOpenState:
        return self.server.state  # type: ignore[attr-defined]

    def _read(self) -> tuple[str, dict[str, list[str]], bytes]:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        return parsed.path, query, body

    def _form(self, body: bytes) -> dict[str, list[str]]:
        return parse_qs(body.decode("utf-8"), keep_blank_values=True)

    def _first(self, values: dict[str, list[str]], key: str) -> str:
        return (values.get(key) or [""])[0]

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization") or ""
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if self.state.expire_once:
            self.state.expire_once = False
            self.state.access_tokens.discard(token)
            _json(self, AUTH_ERROR)
            return False
        if self.state.fail_99_once:
            self.state.fail_99_once = False
            _json(self, {"state": False, "code": 99, "message": "token 失效"})
            return False
        if token not in self.state.access_tokens:
            _json(self, AUTH_ERROR)
            return False
        return True

    def do_GET(self) -> None:
        path, query, _ = self._read()
        if path == "/open/user/info":
            if not self._authorized():
                return
            _json(self, _ok({"user_id": 42, "user_name": "开放用户"}))
            return
        if path == "/open/ufile/files":
            if not self._authorized():
                return
            folder_id = self._first(query, "cid") or "0"
            rows = list(self.state.entries.get(folder_id, []))
            offset = int(self._first(query, "offset") or 0)
            limit = int(self._first(query, "limit") or 1150)
            _json(self, {**_ok(rows[offset: offset + limit]), "count": len(rows)})
            return
        if path == "/open/offline/get_task_list":
            if not self._authorized():
                return
            tasks = []
            for url, task in self.state.tasks.items():
                status = task["status"]
                if status in {0, 1}:
                    task["calls"] += 1
                    status = 1 if task["calls"] == 1 else 2
                    task["status"] = status
                tasks.append(
                    {
                        "info_hash": task["info_hash"],
                        "name": task.get("name") or url,
                        "size": 100,
                        "url": url,
                        "status": status,
                        "percentDone": 100 if status == 2 else 50,
                        "file_id": "file-1",
                        "wp_path_id": task["folder_id"],
                        "error_msg": "模拟失败" if status == -1 else "",
                    }
                )
            _json(
                self,
                _ok({"page": 1, "page_count": 1, "count": len(tasks), "tasks": tasks}),
            )
            return
        _json(self, {"state": False, "message": "未知接口"}, 404)

    def do_POST(self) -> None:
        path, _, body = self._read()
        form = self._form(body)
        if path == "/open/refreshToken":
            token = self._first(form, "refresh_token")
            if self.state.refresh_delay:
                import time

                time.sleep(self.state.refresh_delay)
            if self.state.refresh_mode == "reauth":
                _json(self, {"state": False, "errno": 40140119, "error": "refresh token 已失效"})
                return
            with self.state._lock:
                if token not in self.state.refresh_tokens:
                    _json(self, {"state": False, "errno": 40140116, "error": "refresh token 无效"})
                    return
                self.state.refresh_tokens.discard(token)
            access, refresh = self.state.issue()
            self.state.refresh_tokens.add(refresh)
            self.state.access_tokens.add(access)
            self.state.refresh_count += 1
            _json(
                self,
                _ok(
                    {
                        "access_token": access,
                        "refresh_token": refresh,
                        "expires_in": 7200,
                    }
                ),
            )
            return
        if path == "/open/folder/add":
            if not self._authorized():
                return
            parent = self._first(form, "pid") or "0"
            name = self._first(form, "file_name")
            if not name:
                _json(self, {"state": False, "message": "目录名为空"})
                return
            folder_id = f"dir-{len(self.state.entries) + 1}"
            self.state.add_folder(folder_id, name, parent)
            _json(self, _ok({"file_name": name, "file_id": folder_id}))
            return
        if path == "/open/offline/add_task_urls":
            if not self._authorized():
                return
            self.state.added_fields.append(form)
            url = self._first(form, "urls")
            if not url:
                _json(self, _ok([]))
                return
            if self.state.duplicate and url in self.state.tasks:
                _json(
                    self,
                    _ok([{"state": False, "code": 10008, "message": "任务已存在"}]),
                )
                return
            match = BTIH_RE.search(url)
            info_hash = match.group(1) if match else hashlib.sha1(url.encode()).hexdigest()
            self.state.tasks[url] = {
                "info_hash": info_hash,
                "folder_id": self._first(form, "wp_path_id") or "0",
                "status": 0,
                "calls": 0,
            }
            _json(
                self,
                _ok(
                    [
                        {
                            "state": True,
                            "code": 0,
                            "url": url,
                            "info_hash": info_hash,
                            "message": "",
                        }
                    ]
                ),
            )
            return
        _json(self, {"state": False, "message": "未知接口"}, 404)


class Mock115OpenServer:
    def __init__(self) -> None:
        self.state = MockOpenState()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def start(self) -> "Mock115OpenServer":
        self.thread.start()
        return self

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> "Mock115OpenServer":
        return self.start()

    def __exit__(self, *args: Any) -> None:
        self.close()
