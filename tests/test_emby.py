"""Emby 自动刷新回归测试。

运行：cd backend && .venv/bin/python ../tests/test_emby.py
使用本地假 Emby HTTP 服务与临时数据库，不访问真实 Emby。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["CINEMINT_DATA_DIR"] = tempfile.mkdtemp(prefix="cinemint-emby-")
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from app.core import emby  # noqa: E402
from app.db import execute, init_db, now, query_one, set_setting  # noqa: E402
from app.security import encrypt  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, str]] = []

    def log_message(self, *args):  # noqa: D401
        return

    def _record(self, method: str) -> None:
        Handler.requests.append((method, self.path, self.headers.get("X-Emby-Token") or ""))

    def do_GET(self):
        self._record("GET")
        if self.path.startswith("/System/Info"):
            body = b'{"ServerName":"FakeEmby","Version":"4.8.0"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        self._record("POST")
        if self.path.startswith("/Library/Refresh"):
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


def main() -> int:
    init_db()
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"

    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        if actual == expected:
            passed += 1
        else:
            print(f"FAIL {name}: expected {expected!r}, got {actual!r}")

    def reset():
        Handler.requests.clear()
        emby._last_refresh = 0.0

    # 未启用：不请求。
    set_setting("emby_enabled", False)
    set_setting("emby_url", base)
    set_setting("emby_api_key", encrypt("secret-key"))
    reset()
    expect("disabled refresh", emby.refresh_library("test"), False)
    expect("disabled no request", Handler.requests, [])

    # 启用后测试连接与刷新，检查路径和鉴权头。
    set_setting("emby_enabled", True)
    reset()
    info = emby.test_connection()
    expect("test server", info.get("server"), "FakeEmby")
    expect("test version", info.get("version"), "4.8.0")
    expect("test request", Handler.requests[0][:2], ("GET", "/System/Info"))
    expect("test token", Handler.requests[0][2], "secret-key")

    reset()
    expect("refresh once", emby.refresh_library("整理完成"), True)
    expect("refresh request", Handler.requests[0][:2], ("POST", "/Library/Refresh"))
    expect("refresh token", Handler.requests[0][2], "secret-key")
    # 冷却期内不重复触发。
    expect("refresh cooldown", emby.refresh_library("again"), False)
    expect("cooldown no request", len(Handler.requests), 1)
    # 手动刷新不受冷却限制。
    reset()
    emby.refresh_now()
    expect("manual refresh", Handler.requests[0][:2], ("POST", "/Library/Refresh"))

    # 缺少配置：不请求。
    set_setting("emby_url", "")
    reset()
    expect("missing url", emby.refresh_library("no config"), False)
    expect("missing url no request", Handler.requests, [])
    set_setting("emby_url", base)

    # 整理后触发：未开启 strm 立即刷新；开启且还有待同步记录时交给 strm 队列。
    execute(
        "INSERT INTO sources(name,type,config,created_at,updated_at) "
        "VALUES('emby-test','local','{}','now','now')"
    )
    source_id = query_one("SELECT id FROM sources ORDER BY id DESC LIMIT 1")["id"]
    execute(
        "INSERT INTO libraries(source_id,name,root_path,target_root,media_type,naming,scrape,"
        "strm_sync_enabled,created_at,updated_at) "
        "VALUES(?,'emby-test','inbox','library','auto','{}','{}',0,?,?)",
        (source_id, now(), now()),
    )
    library_id = query_one("SELECT id FROM libraries ORDER BY id DESC LIMIT 1")["id"]

    reset()
    expect("organize refresh without strm", emby.refresh_after_organize(library_id), True)
    expect("organize request", Handler.requests[0][:2], ("POST", "/Library/Refresh"))

    execute("UPDATE libraries SET strm_sync_enabled=1 WHERE id=?", (library_id,))
    execute(
        "INSERT INTO strm_sync_queue(library_id,target_path,relative_dir,file_stem,status,"
        "attempts,force,created_at,updated_at) VALUES(?,?,?,'stem','pending',0,1,?,?)",
        (library_id, "library/a.mkv", "a", now(), now()),
    )
    reset()
    expect("organize waits for strm", emby.refresh_after_organize(library_id), False)
    expect("waiting no request", Handler.requests, [])

    execute("UPDATE strm_sync_queue SET status='done' WHERE library_id=?", (library_id,))
    reset()
    expect("refresh after strm", emby.refresh_after_strm(library_id), True)
    expect("strm request", Handler.requests[0][:2], ("POST", "/Library/Refresh"))

    server.shutdown()
    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
