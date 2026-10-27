"""OpenList 目录缓存刷新回归测试（内置 mock 服务器）。

运行：cd backend && .venv/bin/python ../tests/test_openlist.py
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core import openlist  # noqa: E402
from app.core import subscriptions  # noqa: E402
from app.core.openlist import OpenListError, refresh_directory  # noqa: E402

STATE: dict[str, object] = {}


def reset() -> None:
    STATE.clear()
    STATE.update(
        logins=[],
        lists=[],
        token="",
        expire_once=False,
        list_error=False,
        login_error=False,
        bearer_only=False,
        reject_all=False,
    )
    openlist._tokens.clear()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.endswith("/api/auth/login"):
            payload = self._body()
            STATE["logins"].append(payload)  # type: ignore[union-attr]
            if not self.path.startswith("/api/") and not self.path.startswith(
                "/alist/api/"
            ):
                self._send(404, {"code": 404, "message": "not found"})
                return
            if STATE["login_error"]:
                self._send(200, {"code": 401, "message": "wrong password"})
                return
            token = f"tok{len(STATE['logins'])}"  # type: ignore[arg-type]
            STATE["token"] = token
            self._send(200, {"code": 200, "data": {"token": token}})
            return
        if self.path.endswith("/api/fs/list"):
            body = self._body()
            auth = self.headers.get("Authorization") or ""
            STATE["lists"].append(  # type: ignore[union-attr]
                {"auth": auth, "body": body}
            )
            if STATE["expire_once"]:
                STATE["expire_once"] = False
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            expected = STATE["token"]
            if STATE["bearer_only"]:
                authorized = auth == f"Bearer {expected}"
            else:
                authorized = auth == expected
            if STATE["reject_all"] or not authorized:
                self._send(200, {"code": 401, "message": "token is invalidated"})
                return
            if STATE["list_error"]:
                self._send(200, {"code": 500, "message": "boom"})
                return
            self._send(200, {"code": 200, "data": {"content": []}})
            return
        self._send(404, {"code": 404, "message": "not found"})


def source(url: str, username: str = "user", password: str = "pass") -> dict:
    return {
        "type": "webdav",
        "config": json.dumps(
            {"url": url, "username": username, "password": password},
            ensure_ascii=False,
        ),
    }


def error_message(fn) -> str:
    try:
        fn()
    except OpenListError as exc:
        return str(exc)
    return "<no error>"


class FakeHooks:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []

    def cancelled(self) -> bool:
        return False

    def log(self, message: str, level: str = "info") -> None:
        self.logs.append((level, message))


class FakeSource:
    def exists(self, path: str) -> bool:
        return True


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    checks: list[tuple[str, object, object]] = []

    def check(name: str, actual, expected) -> None:
        checks.append((name, actual, expected))

    # 非 WebDAV / 非 OpenList 路径：直接跳过，不发请求
    reset()
    check("local 源跳过", refresh_directory({"type": "local", "config": "{}"}, "a"), False)
    check("非 /dav 子路径跳过", refresh_directory(source(base + "/dav/115"), "a"), False)
    check("跳过时不发请求", (STATE["logins"], STATE["lists"]), ([], []))

    # 成功刷新：登录 + fs/list refresh
    reset()
    target = "115/待整理/_订阅/测试"
    check("刷新成功", refresh_directory(source(base + "/dav"), target), True)
    check(
        "登录请求",
        list(STATE["logins"]),  # type: ignore[arg-type]
        [{"username": "user", "password": "pass"}],
    )
    check(
        "刷新请求",
        list(STATE["lists"]),  # type: ignore[arg-type]
        [
            {
                "auth": "tok1",
                "body": {
                    "path": "/" + target,
                    "password": "",
                    "page": 1,
                    "per_page": 1,
                    "refresh": True,
                },
            }
        ],
    )

    # 同一账号复用 token
    check("二次刷新成功", refresh_directory(source(base + "/dav"), target), True)
    check(
        "复用 token",
        (
            len(STATE["logins"]),  # type: ignore[arg-type]
            [item["auth"] for item in STATE["lists"]],  # type: ignore[union-attr]
        ),
        (1, ["tok1", "tok1"]),
    )

    # token 失效后重新登录一次（HTTP 401 与 code 401 都视为失效）
    reset()
    STATE["expire_once"] = True
    check("401 后重登成功", refresh_directory(source(base + "/dav"), target), True)
    check(
        "重登请求两次",
        (
            len(STATE["logins"]),
            [item["auth"] for item in STATE["lists"]],
        ),
        (2, ["tok1", "Bearer tok1", "tok2"]),
    )

    # 只认 Bearer 的老版本 AList：裸 token 失败后自动降级
    reset()
    STATE["bearer_only"] = True
    check("Bearer 兼容", refresh_directory(source(base + "/dav"), target), True)
    check(
        "Bearer 降级顺序",
        (
            len(STATE["logins"]),
            [item["auth"] for item in STATE["lists"]],
        ),
        (1, ["tok1", "Bearer tok1"]),
    )

    # 重登后仍然失效：放弃并给出提示
    reset()
    STATE["reject_all"] = True
    check(
        "持续失效提示",
        error_message(lambda: refresh_directory(source(base + "/dav"), target)),
        "登录状态已失效，无法刷新目录",
    )
    check("持续失效最多登录两次", len(STATE["logins"]), 2)
    check("持续失效尝试两次鉴权", len(STATE["lists"]), 4)

    # 密码错误 / 刷新失败
    reset()
    STATE["login_error"] = True
    check(
        "密码错误提示",
        error_message(lambda: refresh_directory(source(base + "/dav"), target)),
        "用户名或密码错误",
    )

    reset()
    STATE["list_error"] = True
    check(
        "刷新失败提示",
        error_message(lambda: refresh_directory(source(base + "/dav"), target)),
        "boom",
    )

    # 不是 OpenList：登录接口 404 时静默跳过
    reset()
    check(
        "非 OpenList 登录 404 跳过",
        refresh_directory(source(base + "/notopenlist/dav"), target),
        False,
    )
    check("404 仍记录一次登录", len(STATE["logins"]), 1)

    # 空目录 / 缺凭据
    reset()
    check(
        "空目录拒绝",
        error_message(lambda: refresh_directory(source(base + "/dav"), "")),
        "目录为空，无法刷新",
    )
    check(
        "缺凭据拒绝",
        error_message(
            lambda: refresh_directory(source(base + "/dav", password=""), target)
        ),
        "文件源没有填写用户名或密码，无法自动刷新目录",
    )

    # 订阅等待阶段会调用刷新（含初始一次）
    reset()
    calls: list[str] = []
    original = subscriptions.refresh_directory
    subscriptions.refresh_directory = lambda row, path: calls.append(path) or True
    try:
        hooks = FakeHooks()
        visible, hidden = subscriptions._wait_visible(
            FakeSource(),
            [{"id": 1, "saved_path": "115/待整理/_订阅/测试/a.mkv"}],
            10,
            hooks,  # type: ignore[arg-type]
            source_row=source(base + "/dav"),
            refresh_path="115/待整理/_订阅/测试",
        )
    finally:
        subscriptions.refresh_directory = original
    check("等待时刷新目录", calls, ["115/待整理/_订阅/测试"])
    check("可见条目", ([entry["id"] for entry in visible], hidden), ([1], []))
    check(
        "刷新日志",
        [item for item in hooks.logs if "刷新" in item[1]],
        [("debug", "已刷新 OpenList 目录缓存：115/待整理/_订阅/测试")],
    )

    server.shutdown()

    passed = 0
    for name, actual, expected in checks:
        if actual == expected:
            passed += 1
        else:
            print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    print(f"{passed}/{len(checks)} cases passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
