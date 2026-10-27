"""115 接口本地假服务，供客户端协议测试使用。"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

BTIH_RE = re.compile(r"btih:([0-9a-zA-Z]+)", re.I)


def task_key(url: str) -> str:
    match = BTIH_RE.search(url or "")
    if match:
        return "btih:" + match.group(1).lower()
    return "url:" + hashlib.sha1(url.encode()).hexdigest()


class Mock115State:
    def __init__(self) -> None:
        self.entries: dict[str, list[dict[str, Any]]] = {"0": []}
        self.counter = 0
        self.tasks: dict[str, dict[str, Any]] = {}
        self.names: dict[str, str] = {}
        self.advance_tasks = True
        self.fail_urls: set[str] = set()
        self.error_mode = ""
        self.duplicate_error = False
        self.requests: list[tuple[str, str, dict[str, list[str]]]] = []
        self.added_fields: list[dict[str, list[str]]] = []
        self._lock = threading.Lock()

    def add_folder(self, folder_id: str, name: str, parent: str = "0") -> str:
        with self._lock:
            self.entries.setdefault(parent, []).append(
                {"cid": folder_id, "n": name, "pid": parent, "s": 0}
            )
            self.entries.setdefault(folder_id, [])
            return folder_id

    def add_file(self, fid: str, name: str, parent: str = "0", size: int = 1024) -> str:
        with self._lock:
            self.entries.setdefault(parent, []).append(
                {"fid": fid, "n": name, "pid": parent, "s": size}
            )
            return fid

    def new_folder_id(self) -> str:
        self.counter += 1
        return f"new-dir-{self.counter}"


def _json(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        return

    @property
    def state(self) -> Mock115State:
        return self.server.state  # type: ignore[attr-defined]

    def _read(self) -> tuple[str, dict[str, list[str]], bytes]:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.state.requests.append((self.command, parsed.path, query))
        return parsed.path, query, body

    def _form(self, body: bytes) -> dict[str, list[str]]:
        return parse_qs(body.decode("utf-8"), keep_blank_values=True)

    def _first(self, values: dict[str, list[str]], key: str) -> str:
        return (values.get(key) or [""])[0]

    def _state_error(self) -> bool:
        if self.state.error_mode == "99":
            _json(self, {"state": False, "errno": 99, "error": "登录失败"})
            return True
        if self.state.error_mode == "911":
            _json(self, {"state": False, "errno": 911, "error": "请验证账号"})
            return True
        if self.state.error_mode == "false":
            _json(self, {"state": False, "error": "模拟失败"})
            return True
        return False

    def do_GET(self) -> None:
        path, query, _ = self._read()
        if self._state_error():
            return
        if path == "/my/":
            _json(self, {"state": True, "nickname": "测试115"})
            return
        if path == "/webapi/files":
            folder_id = self._first(query, "cid") or "0"
            rows = list(self.state.entries.get(folder_id, []))
            offset = int(self._first(query, "offset") or 0)
            limit = int(self._first(query, "limit") or 1150)
            _json(
                self,
                {
                    "state": True,
                    "cid": folder_id,
                    "count": len(rows),
                    "data": rows[offset: offset + limit],
                },
            )
            return
        if path == "/site/":
            if self._first(query, "ct") == "offline" and self._first(query, "ac") == "space":
                _json(self, {"state": True, "sign": "test-sign", "time": 123456})
            else:
                _json(self, {"state": False, "error": "未知接口"})
            return
        if path == "/lixian/web/lixian/":
            if self._first(query, "ac") != "task_lists":
                _json(self, {"state": False, "error": "未知接口"})
                return
            tasks = []
            for url, task in self.state.tasks.items():
                status = task["status"]
                if self.state.advance_tasks and status in {0, 1}:
                    task["calls"] += 1
                    status = 1 if task["calls"] == 1 else 2
                    task["status"] = status
                if url in self.state.fail_urls:
                    status = -1
                move = -1 if task.get("move_failed") else 0
                tasks.append(
                    {
                        "info_hash": task["info_hash"],
                        "name": self.state.names.get(url, url),
                        "size": 100,
                        "url": url,
                        "status": status,
                        "percentDone": 100 if status == 2 else 50,
                        "file_id": "file-1",
                        "wp_path_id": task["folder_id"],
                        "move": move,
                        "error_msg": "模拟失败" if status == -1 else "",
                    }
                )
            _json(
                self,
                {"state": True, "page": 1, "page_count": 1, "count": len(tasks), "tasks": tasks},
            )
            return
        _json(self, {"state": False, "error": "未知接口"}, 404)

    def do_POST(self) -> None:
        path, _, body = self._read()
        form = self._form(body)
        if path == "/webapi/files/add":
            if self._state_error():
                return
            parent = self._first(form, "pid") or "0"
            name = self._first(form, "cname")
            if not name:
                _json(self, {"state": False, "error": "目录名为空"})
                return
            folder_id = self.state.new_folder_id()
            self.state.add_folder(folder_id, name, parent)
            _json(self, {"state": True, "cid": folder_id, "cname": name})
            return
        if path == "/site/web/lixian/":
            if self._first(form, "sign") != "test-sign":
                _json(self, {"state": False, "error": "签名无效"})
                return
            if self._state_error():
                return
            self.state.added_fields.append(form)
            url = self._first(form, "url[0]")
            if not url:
                _json(self, {"state": True, "result": []})
                return
            if self.state.duplicate_error and url in self.state.tasks:
                _json(
                    self,
                    {
                        "state": True,
                        "result": [{"state": False, "error_msg": "任务已存在"}],
                    },
                )
                return
            match = BTIH_RE.search(url)
            if match:
                info_hash = match.group(1)
            else:
                info_hash = hashlib.sha1(url.encode()).hexdigest()
            self.state.tasks[url] = {
                "info_hash": info_hash,
                "folder_id": self._first(form, "wp_path_id") or "0",
                "status": 0,
                "calls": 0,
            }
            _json(
                self,
                {
                    "state": True,
                    "result": [
                        {
                            "state": True,
                            "url": url,
                            "info_hash": info_hash,
                            "error_msg": "",
                        }
                    ],
                },
            )
            return
        _json(self, {"state": False, "error": "未知接口"}, 404)


class Mock115Server:
    def __init__(self) -> None:
        self.state = Mock115State()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def start(self) -> "Mock115Server":
        self.thread.start()
        return self

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> "Mock115Server":
        return self.start()

    def __exit__(self, *args: Any) -> None:
        self.close()
