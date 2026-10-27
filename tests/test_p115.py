"""115 客户端协议测试：目录、离线添加、任务状态与错误映射。

运行：cd backend && .venv/bin/python ../tests/test_p115.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["CINEFLOW_DATA_DIR"] = tempfile.mkdtemp(prefix="cineflow-p115-")
os.environ["CINEFLOW_DEMO"] = "false"

from p115_mock import Mock115Server, task_key  # noqa: E402

from app.core.p115 import P115Client, P115Error  # noqa: E402

COOKIE = "UID=123_A1_1700000000; CID=dev-1; SEID=session-1; KID=key-1"


def client(server: Mock115Server) -> P115Client:
    return P115Client(
        COOKIE,
        qps=50,
        webapi=server.base + "/webapi",
        site=server.base + "/site",
        lixian=server.base + "/lixian",
        my=server.base + "/my",
    )


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

    def raises(name: str, fn, contains: str = "") -> None:
        nonlocal passed, total
        total += 1
        try:
            fn()
        except P115Error as exc:
            if contains and contains not in str(exc):
                print(f"FAIL  {name}\n  expected message containing: {contains!r}\n  actual: {exc!r}")
                return
            passed += 1
        except Exception as exc:
            print(f"FAIL  {name}\n  unexpected exception: {exc!r}")
        else:
            print(f"FAIL  {name}\n  expected P115Error")

    raises("missing cookie", lambda: P115Client("UID=1; CID=2; SEID=3"), "KID")

    with Mock115Server() as server:
        state = server.state
        state.add_folder("dir-1", "待整理")
        state.add_file("file-1", "movie.mkv", parent="dir-1")

        with client(server) as p115:
            result = p115.test()
            expect("test nickname", result.get("nickname"), "测试115")

            folder_id = p115.ensure_folder_path(["待整理", "_订阅", "API"])
            expect("ensure folder id", bool(folder_id), True)
            expect(
                "ensure folder created",
                [entry["name"] for entry in p115.list_subfolders(["待整理"])],
                ["_订阅"],
            )
            subfolders = p115.list_subfolders(["待整理", "_订阅"])
            expect("folder children", [entry["name"] for entry in subfolders], ["API"])
            entries = p115.list_entries("dir-1")
            expect("file entry name", entries[0]["name"], "movie.mkv")
            expect("file entry dir flag", entries[0]["is_dir"], False)
            expect("file entry size", entries[0]["size"], 1024)

            magnet = "magnet:?xt=urn:btih:ABCDEF0123456789&dn=Test"
            task = p115.add_task(magnet, folder_id=folder_id)
            expect("magnet task id", task["id"], "btih:abcdef0123456789")
            expect("magnet request uid", state.added_fields[0]["uid"][0], "123_A1_1700000000")
            expect("magnet request folder", state.added_fields[0]["wp_path_id"][0], folder_id)
            expect("magnet sign", state.added_fields[0]["sign"][0], "test-sign")

            state.names[magnet] = "Test Show S01E01"
            tasks = p115.list_tasks()
            expect("task count", len(tasks), 1)
            expect("task first state", tasks[0]["status"], "downloading")
            expect("task first percent", tasks[0]["percent"], 50.0)
            tasks = p115.list_tasks()
            expect("task done state", tasks[0]["status"], "done")
            expect("task done name", tasks[0]["name"], "Test Show S01E01")
            expect("task done file", tasks[0]["file_id"], "file-1")
            expect("task done folder", tasks[0]["folder_id"], folder_id)

            state.duplicate_error = True
            duplicate = p115.add_task(magnet, folder_id=folder_id)
            expect("duplicate task id", duplicate["id"], task_key(magnet))

            torrent = "https://example.com/2.torrent"
            torrent_task = p115.add_task(torrent, folder_id=folder_id)
            expect("torrent task id", torrent_task["id"], task_key(torrent))
            state.fail_urls.add(torrent)
            failed = next(
                task for task in p115.list_tasks() if task["id"] == task_key(torrent)
            )
            expect("failed task state", failed["status"], "failed")
            expect("failed task error", bool(failed["error"]), True)

            state.error_mode = "99"
            raises("cookie expired", lambda: p115.list_entries("0"), "Cookie")
            state.error_mode = "911"
            raises("risk control", lambda: p115.list_entries("0"), "风控")
            state.error_mode = "false"
            raises("state false", lambda: p115.list_entries("0"), "模拟失败")
            state.error_mode = ""

            state.tasks[torrent]["move_failed"] = True
            state.fail_urls.discard(torrent)
            moved = next(
                task for task in p115.list_tasks() if task["id"] == task_key(torrent)
            )
            expect("move failed state", moved["status"], "failed")
            expect("move failed error", "移动" in moved["error"], True)

    # 任务 ID 与 add_task 返回一致
    expect(
        "torrent key stable",
        task_key("https://example.com/2.torrent"),
        "url:" + hashlib.sha1(b"https://example.com/2.torrent").hexdigest(),
    )

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
