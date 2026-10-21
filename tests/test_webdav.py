"""WebDAV 客户端回归测试（内置 mock 服务器，模拟 OpenList / AList 关键行为）。

运行：cd backend && .venv/bin/python ../tests/test_webdav.py
"""

from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.providers.base import SourceError  # noqa: E402
from app.core.providers.base import norm  # noqa: E402
from app.core.providers.webdav import WebDavFileSource  # noqa: E402

AUTH = "Basic dXNlcjpwYXNz"  # user:pass
STATE = {"allow_write": False}

MOVIE_DIR = quote("电影")
MOVIE_FILE = quote("让子弹飞.2010.1080p.mkv")

ROOT_LISTING = f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response><D:href>/dav/</D:href><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>
  <D:response><D:href>/dav/{MOVIE_DIR}/</D:href><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>
  <D:response><D:href>/dav/test.mkv</D:href><D:propstat><D:prop><D:resourcetype/><D:getcontentlength>1024</D:getcontentlength></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>
</D:multistatus>
"""

MOVIE_LISTING = f"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response><D:href>/dav/{MOVIE_DIR}/</D:href><D:propstat><D:prop><D:resourcetype><D:collection/></D:resourcetype></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>
  <D:response><D:href>/dav/{MOVIE_DIR}/{MOVIE_FILE}</D:href><D:propstat><D:prop><D:resourcetype/><D:getcontentlength>2048</D:getcontentlength></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>
</D:multistatus>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes = b"", content_type: str = "") -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_xml(self, body: str) -> None:
        self._send(207, body.encode("utf-8"), "application/xml; charset=utf-8")

    def _require_auth(self) -> bool:
        if self.headers.get("Authorization") == AUTH:
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="restricted"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _drain_body(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)

    def do_PROPFIND(self):
        # 模拟 OpenList：裸地址（/）不接受 PROPFIND，返回 405
        if self.path == "/":
            self._send(405)
            return
        if not self._require_auth():
            return
        stripped = self.path.rstrip("/")
        if stripped == "/dav":
            self._send_xml(ROOT_LISTING)
        elif stripped == f"/dav/{MOVIE_DIR}":
            self._send_xml(MOVIE_LISTING)
        elif stripped == "/dav/limited":
            self._send(429)
        else:
            self._send(404)

    def do_MKCOL(self):
        if not self._require_auth():
            return
        self._send(201 if STATE["allow_write"] else 403, b"created")

    def do_PUT(self):
        self._drain_body()
        if not self._require_auth():
            return
        self._send(201 if STATE["allow_write"] else 403)

    def do_MOVE(self):
        if not self._require_auth():
            return
        self._send(201 if STATE["allow_write"] else 403)

    def do_DELETE(self):
        if not self._require_auth():
            return
        self._send(204 if STATE["allow_write"] else 403, b"")


class MoveSim(WebDavFileSource):
    """不联网的 MOVE 模拟：记录操作顺序，并模拟部分成功 / 改名残留。"""

    def __init__(self) -> None:
        super().__init__({"url": "http://example.invalid/dav"})
        self.files: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.fail_targets: set[str] = set()
        self.move_then_error: set[str] = set()

    def mkdir(self, path: str) -> None:
        return

    def exists(self, path: str) -> bool:
        return norm(path) in self.files

    def _move_once(self, source: str, target: str) -> None:
        source, target = norm(source), norm(target)
        self.calls.append((source, target))
        if source not in self.files:
            raise SourceError(f"移动失败 {source}: HTTP 404")
        if target in self.fail_targets:
            raise SourceError(f"移动失败 {source}: HTTP 500")
        self.files.discard(source)
        self.files.add(target)
        if target in self.move_then_error:
            raise SourceError(f"移动失败 {source}: HTTP 500")


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def error_message(fn) -> str:
    try:
        fn()
    except SourceError as exc:
        return str(exc)
    return "<no error>"


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    passed = 0
    total = 0

    def check_count(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        if check(name, actual, expected):
            passed += 1

    # ---- 裸地址自动补全 /dav（OpenList 场景） ----
    bare = WebDavFileSource({"url": base, "username": "user", "password": "pass"})
    result = bare.test()
    check_count(
        "裸地址自动补全 /dav",
        (result.get("ok"), result.get("entries"), result.get("normalized_url")),
        (True, 2, f"{base}/dav"),
    )
    msg = error_message(lambda: bare.list(""))
    check_count("裸地址 list 报错含 405 与 /dav 提示", ("405" in msg) and ("/dav" in msg), True)
    bare.client.close()

    # ---- 显式 /dav ----
    source = WebDavFileSource({"url": base + "/dav", "username": "user", "password": "pass"})
    result = source.test()
    check_count(
        "显式 /dav 连通",
        (result.get("ok"), result.get("entries"), "normalized_url" in result),
        (True, 2, False),
    )

    # ---- 列表 / stat ----
    check_count(
        "根目录列表（中文名解码、目录标识、大小）",
        [(e.name, e.is_dir, e.size) for e in source.list("")],
        [("电影", True, 0), ("test.mkv", False, 1024)],
    )
    check_count(
        "子目录列表",
        [(e.name, e.is_dir, e.size) for e in source.list("电影")],
        [("让子弹飞.2010.1080p.mkv", False, 2048)],
    )
    entry = source.stat("电影/让子弹飞.2010.1080p.mkv")
    check_count(
        "stat 文件",
        (entry.name, entry.is_dir, entry.size) if entry else None,
        ("让子弹飞.2010.1080p.mkv", False, 2048),
    )

    # ---- 认证失败 ----
    bad = WebDavFileSource({"url": base + "/dav", "username": "user", "password": "wrong"})
    check_count("错误密码提示", error_message(bad.test), "认证失败，请检查用户名或密码")
    bad.client.close()

    # ---- 空密码：给出「用户名或密码为空」提示 ----
    empty = WebDavFileSource({"url": base + "/dav", "username": "user", "password": ""})
    check_count(
        "空密码提示",
        error_message(empty.test),
        "认证失败：用户名或密码为空，请先填写 OpenList/WebDAV 账号密码",
    )
    empty.client.close()

    # ---- 429 限流提示（OpenList 连续登录失败后） ----
    limited = WebDavFileSource({"url": base + "/dav/limited", "username": "user", "password": "pass"})
    msg = error_message(limited.test)
    check_count("429 限流提示", ("429" in msg) and ("限流" in msg), True)
    limited.client.close()

    # ---- 写入权限提示（OpenList 未开启 WebDAV 管理时返回 403） ----
    STATE["allow_write"] = False
    msg = error_message(lambda: source.mkdir("新建目录"))
    check_count("写入 403 提示 WebDAV 管理", ("403" in msg) and ("WebDAV 管理" in msg), True)

    # ---- 写入 / 移动 / 删除成功路径 ----
    STATE["allow_write"] = True

    def run_writes() -> None:
        source.mkdir("新建目录")
        source.write("新建目录/a.nfo", b"<nfo/>")
        source.move("电影/a.mkv", "新建目录/b.mkv")
        source.delete("新建目录/b.mkv")

    check_count("写入 / 移动 / 删除成功", error_message(run_writes), "<no error>")
    source.client.close()

    # ---- 跨目录移动：先在同目录改名，避免 OpenList + 夸克移动后改名瞬时 500 ----
    sim = MoveSim()
    sim.files = {"待整理/a.mkv"}
    sim.move("待整理/a.mkv", "library/电影/b.mkv")
    check_count(
        "跨目录移动先改名再移动",
        (sim.calls, sim.files),
        (
            [("待整理/a.mkv", "待整理/b.mkv"), ("待整理/b.mkv", "library/电影/b.mkv")],
            {"library/电影/b.mkv"},
        ),
    )
    sim.client.close()

    # 服务端已完成移动但返回 500：应按成功处理，不回滚
    sim = MoveSim()
    sim.files = {"待整理/a.mkv"}
    sim.move_then_error = {"library/电影/b.mkv"}
    check_count(
        "移动后返回 500 仍视为成功",
        (error_message(lambda: sim.move("待整理/a.mkv", "library/电影/b.mkv")), sim.files),
        ("<no error>", {"library/电影/b.mkv"}),
    )
    sim.client.close()

    # 上次失败留下「已改名但未移动」的源文件：直接继续移动
    sim = MoveSim()
    sim.files = {"待整理/b.mkv"}
    check_count(
        "续做上次残留的改名文件",
        (error_message(lambda: sim.move("待整理/a.mkv", "library/电影/b.mkv")), sim.files),
        ("<no error>", {"library/电影/b.mkv"}),
    )
    sim.client.close()

    # 上次已经完整移动：源和目标状态一致时直接成功
    sim = MoveSim()
    sim.files = {"library/电影/b.mkv"}
    check_count(
        "已移动完成时重试不报错",
        error_message(lambda: sim.move("待整理/a.mkv", "library/电影/b.mkv")),
        "<no error>",
    )
    sim.client.close()

    # 第二步移动失败：把源文件名恢复，便于用户直接重试
    sim = MoveSim()
    sim.files = {"待整理/a.mkv"}
    sim.fail_targets = {"library/电影/b.mkv"}
    msg = error_message(lambda: sim.move("待整理/a.mkv", "library/电影/b.mkv"))
    check_count(
        "移动失败后回滚改名",
        ("500" in msg, sim.files),
        (True, {"待整理/a.mkv"}),
    )
    sim.client.close()

    server.shutdown()
    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
