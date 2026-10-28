"""通知适配器协议测试：用本地 HTTP 服务校验请求路径、认证与载荷。

运行：cd backend && .venv/bin/python ../tests/test_notification_adapters.py
不访问真实第三方服务。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["CINEMINT_DATA_DIR"] = tempfile.mkdtemp(prefix="cinemint-notification-adapters-")
os.environ["CINEMINT_DEMO"] = "false"

from app.core.notifications import NotificationError, send_to_config  # noqa: E402


class Capture:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        return

    @property
    def capture(self) -> Capture:
        return self.server.capture  # type: ignore[attr-defined]

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        form = {
            key: values[-1] for key, values in parse_qs(raw.decode("utf-8")).items()
        }
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        with self.capture.lock:
            self.capture.requests.append(
                {
                    "path": parsed.path,
                    "headers": dict(self.headers),
                    "payload": payload,
                    "form": form,
                }
            )
        if parsed.path == "/chat/contact":
            self._json({"code": 0, "data": {}})
        elif parsed.path == "/chat/room":
            self._json({"code": 0, "data": {}})
        elif parsed.path == "/v1/message.create":
            self._json([{"id": "message-1", "content": payload.get("content", "")}])
        elif parsed.path == "/api/v2/channel/message/send":
            self._json({"status": 0, "message": "success", "data": {"messageId": "m1"}})
        elif parsed.path == "/pushme-fail":
            self._text("请检查push_key是否正确!")
        elif parsed.path == "/pushme":
            self._text("success")
        elif parsed.path.startswith("/v3/send/"):
            self._json({"success": True, "code": 0, "message": "操作成功"})
        else:
            self._json({"code": 200, "message": "ok"})

    def _json(self, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        if actual == expected:
            passed += 1
        else:
            print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")

    capture = Capture()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.capture = capture  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def last() -> dict[str, Any]:
        return capture.requests[-1]

    try:
        # ---------- 微加机器人 ----------
        send_to_config(
            "weplus_bot",
            {
                "endpoint": base + "/send",
                "token": "tp-token",
                "receiver": "group-1",
                "template": "html",
                "version": "personal",
            },
            "标题",
            "内容",
        )
        request = last()
        expect("weplus path", request["path"], "/send")
        expect(
            "weplus payload",
            request["payload"],
            {
                "token": "tp-token",
                "title": "标题",
                "content": "内容",
                "receiver": "group-1",
                "template": "html",
                "version": "personal",
            },
        )

        # ---------- 智能微秘书（联系人 / 群） ----------
        send_to_config(
            "wechaty",
            {
                "endpoint": base + "/chat",
                "api_key": "aibotk-key",
                "target_type": "contact",
                "target": "文件传输助手",
            },
            "标题",
            "内容",
        )
        request = last()
        expect("aibotk contact path", request["path"], "/chat/contact")
        expect(
            "aibotk contact payload",
            request["payload"],
            {
                "apiKey": "aibotk-key",
                "name": "文件传输助手",
                "message": {"type": 1, "content": "标题\n内容"},
            },
        )

        send_to_config(
            "wechaty",
            {
                "endpoint": base + "/chat",
                "api_key": "aibotk-key",
                "target_type": "room",
                "target": "追更群",
            },
            "标题",
            "内容",
        )
        request = last()
        expect("aibotk room path", request["path"], "/chat/room")
        expect("aibotk room key", request["payload"].get("roomName"), "追更群")

        # ---------- Chronocat / Satori ----------
        send_to_config(
            "chronocat",
            {
                "endpoint": base,
                "token": "satori-token",
                "platform": "chronocat",
                "self_id": "10001",
                "channel_id": "channel-1",
            },
            "更新 & 通知",
            "第 1 集 <新>",
        )
        request = last()
        expect("chronocat path", request["path"], "/v1/message.create")
        expect(
            "chronocat auth",
            request["headers"].get("Authorization"),
            "Bearer satori-token",
        )
        expect(
            "chronocat platform", request["headers"].get("Satori-Platform"), "chronocat"
        )
        expect(
            "chronocat user", request["headers"].get("Satori-User-Id"), "10001"
        )
        expect("chronocat channel", request["payload"].get("channel_id"), "channel-1")
        expect(
            "chronocat content",
            request["payload"].get("content"),
            "<message><text>更新 &amp; 通知\n第 1 集 &lt;新&gt;</text></message>",
        )

        # ---------- DoDo ----------
        send_to_config(
            "dodo",
            {
                "endpoint": base,
                "client_id": "1001",
                "token": "dodo-token",
                "channel_id": "channel-2",
            },
            "标题",
            "内容",
        )
        request = last()
        expect("dodo path", request["path"], "/api/v2/channel/message/send")
        expect(
            "dodo auth",
            request["headers"].get("Authorization"),
            "Bot 1001.dodo-token",
        )
        expect("dodo body", request["payload"].get("messageType"), 1)
        expect(
            "dodo content",
            request["payload"].get("messageBody", {}).get("content"),
            "标题\n内容",
        )

        # ---------- PushMe ----------
        send_to_config(
            "pushme",
            {"endpoint": base + "/pushme", "push_key": "pm-key"},
            "标题",
            "内容",
        )
        request = last()
        expect("pushme form key", request["form"].get("push_key"), "pm-key")
        expect("pushme form title", request["form"].get("title"), "标题")
        failed = False
        try:
            send_to_config(
                "pushme",
                {"endpoint": base + "/pushme-fail", "push_key": "pm-key"},
                "标题",
                "内容",
            )
        except NotificationError:
            failed = True
        expect("pushme failure detected", failed, True)

        # ---------- Qmsg v3 ----------
        send_to_config(
            "qmsg",
            {"server": base, "key": "qmsg-key", "group": "123456"},
            "标题",
            "内容",
        )
        request = last()
        expect("qmsg path", request["path"], "/v3/send/qmsg-key")
        expect("qmsg message", request["form"].get("msg"), "标题\n内容")
        expect("qmsg group", request["form"].get("group"), "123456")
    finally:
        server.shutdown()
        server.server_close()

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
