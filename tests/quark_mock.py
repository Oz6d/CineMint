"""夸克接口的本地假服务，供订阅测试使用。

只实现 CineMint 调用的端点；转存时把文件写到 save_root，
用来模拟 OpenList 已经能看到转存结果。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


class MockQuarkState:
    def __init__(self) -> None:
        self.share_files: dict[str, dict[str, Any]] = {}
        self.my_files: dict[str, list[dict[str, Any]]] = {"0": []}
        self.save_root: Path | None = None
        self.require_passcode = ""
        self.passcode = ""
        self.share_token_error: tuple[str, int] | None = None
        self.member_unavailable = False
        self.fail_save = False
        self.requests: list[tuple[str, str, dict[str, str], dict[str, Any]]] = []
        self.task_fids: list[str] = []
        self._counter = 0
        self._lock = threading.Lock()

    def add_folder(
        self, name: str, parent: str = "0", fid: str = "", *, my_drive: bool = False
    ) -> str:
        with self._lock:
            self._counter += 1
            fid = fid or f"dir-{self._counter}"
            entry = {"fid": fid, "name": name, "dir": True, "parent": parent, "token": ""}
            if my_drive:
                self.my_files.setdefault(parent, []).append(
                    {"fid": fid, "file_name": name, "dir": True, "size": 0}
                )
            else:
                self.share_files[fid] = entry
            return fid

    def add_file(
        self,
        name: str,
        parent: str = "0",
        fid: str = "",
        *,
        size: int = 1024,
        content: bytes | None = None,
    ) -> str:
        with self._lock:
            self._counter += 1
            fid = fid or f"file-{self._counter}"
            self.share_files[fid] = {
                "fid": fid,
                "name": name,
                "dir": False,
                "parent": parent,
                "token": f"token-{fid}",
                "size": size,
                "content": content if content is not None else f"content:{fid}".encode(),
            }
            return fid


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
    def state(self) -> MockQuarkState:
        return self.server.state  # type: ignore[attr-defined]

    def _params(self) -> tuple[str, dict[str, str], dict[str, Any]]:
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/1/clouddrive/"):
            path = path[len("/1/clouddrive/"):]
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        body: dict[str, Any] = {}
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
        self.state.requests.append((self.command, path, query, body))
        return path, query, body

    def _ok(self, data: Any) -> None:
        _json(self, {"status": 200, "code": 0, "message": "ok", "data": data})

    def _error(self, message: str, code: int = 500) -> None:
        _json(self, {"status": 200, "code": code, "message": message, "data": {}})

    def do_POST(self) -> None:
        path, _, body = self._params()
        if path == "share/sharepage/token":
            if self.state.share_token_error is not None:
                message, code = self.state.share_token_error
                self._error(message, code)
                return
            if self.state.require_passcode and body.get("passcode") != self.state.passcode:
                self._error("提取码错误", 31001)
                return
            self._ok({"stoken": "test-stoken"})
            return
        if path == "share/sharepage/save":
            if self.state.fail_save:
                self._error("模拟转存失败")
                return
            dest = str(body.get("to_pdir_fid") or "0")
            saved: list[str] = []
            with self.state._lock:
                for fid in body.get("fid_list") or []:
                    entry = self.state.share_files.get(str(fid))
                    if not entry:
                        continue
                    self.state._counter += 1
                    new_fid = f"saved-{self.state._counter}"
                    name = entry["name"]
                    if self.state.save_root is not None:
                        target = self.state.save_root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(entry.get("content") or b"")
                    self.state.my_files.setdefault(dest, []).append(
                        {"fid": new_fid, "file_name": name, "dir": False, "size": entry["size"]}
                    )
                    saved.append(new_fid)
                self.state.task_fids = saved
            self._ok({"task_id": "task-1", "save_as": {"save_as_top_fids": saved}})
            return
        if path == "file":
            parent = str(body.get("pdir_fid") or "0")
            name = str(body.get("file_name") or "")
            with self.state._lock:
                self.state._counter += 1
                fid = f"folder-{self.state._counter}"
                self.state.my_files.setdefault(parent, []).append(
                    {"fid": fid, "file_name": name, "dir": True, "size": 0}
                )
            self._ok({"fid": fid})
            return
        if path == "file/delete":
            fids = {str(fid) for fid in body.get("filelist") or []}
            with self.state._lock:
                for folder, entries in self.state.my_files.items():
                    self.state.my_files[folder] = [
                        entry for entry in entries if entry["fid"] not in fids
                    ]
            self._ok({})
            return
        self._error(f"未知接口: {path}", 404)

    def do_GET(self) -> None:
        path, query, _ = self._params()
        if path == "member":
            if self.state.member_unavailable:
                self._error("Not Found", 404)
                return
            self._ok({"member_type": "SUPER_VIP", "nickname": "测试账号"})
            return
        if path == "account/info":
            self._ok({"nickname": "测试账号"})
            return
        if path == "share/sharepage/detail":
            parent = str(query.get("pdir_fid") or "0")
            page = max(1, int(query.get("_page") or 1))
            size = max(1, int(query.get("_size") or 200))
            items = [
                entry
                for entry in self.state.share_files.values()
                if str(entry["parent"]) == parent
            ]
            items.sort(key=lambda entry: (not entry["dir"], entry["name"].lower()))
            total = len(items)
            sliced = items[(page - 1) * size: page * size]
            self._ok(
                {
                    "list": [
                        {
                            "fid": entry["fid"],
                            "file_name": entry["name"],
                            "dir": entry["dir"],
                            "size": entry.get("size") or 0,
                            "share_fid_token": entry.get("token") or "",
                        }
                        for entry in sliced
                    ],
                    "metadata": {"_total": total},
                }
            )
            return
        if path == "task":
            self._ok(
                {
                    "status": 2,
                    "save_as": {"save_as_top_fids": self.state.task_fids},
                }
            )
            return
        if path == "file/sort":
            parent = str(query.get("pdir_fid") or "0")
            page = max(1, int(query.get("_page") or 1))
            size = max(1, int(query.get("_size") or 200))
            items = list(self.state.my_files.get(parent, []))
            items.sort(key=lambda entry: (not entry["dir"], entry["file_name"].lower()))
            sliced = items[(page - 1) * size: page * size]
            self._ok(
                {
                    "list": [
                        {
                            "fid": entry["fid"],
                            "file_name": entry["file_name"],
                            "dir": entry["dir"],
                            "size": entry.get("size") or 0,
                        }
                        for entry in sliced
                    ],
                    "metadata": {"_total": len(items)},
                }
            )
            return
        self._error(f"未知接口: {path}", 404)


class MockQuarkServer:
    def __init__(self) -> None:
        self.state = MockQuarkState()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/1/clouddrive"

    def start(self) -> "MockQuarkServer":
        self.thread.start()
        return self

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> "MockQuarkServer":
        return self.start()

    def __exit__(self, *args: Any) -> None:
        self.close()
