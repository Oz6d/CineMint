"""115 开放平台测试：令牌刷新轮换、并发去重、鉴权重试与接口映射。

运行：cd backend && .venv/bin/python ../tests/test_p115_open.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["CINEFLOW_DATA_DIR"] = tempfile.mkdtemp(prefix="cineflow-p115-open-")
os.environ["CINEFLOW_DEMO"] = "false"

from p115_open_mock import Mock115OpenServer, task_key  # noqa: E402

import app.core.p115_open as p115_open  # noqa: E402
from app.core.p115 import P115Error  # noqa: E402
from app.core.p115_open import (  # noqa: E402
    P115AuthError,
    P115OpenClient,
    access_token,
    authorize_with_refresh_token,
    build_p115_client,
    clear_token,
    refresh_now,
    token_status,
)
from app.db import init_db, set_setting  # noqa: E402


def main() -> int:
    init_db()
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        if actual == expected:
            passed += 1
        else:
            print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")

    def raises(name: str, fn, kind=P115Error, contains: str = "") -> None:
        nonlocal passed, total
        total += 1
        try:
            fn()
        except kind as exc:
            if contains and contains not in str(exc):
                print(f"FAIL  {name}\n  expected message containing: {contains!r}\n  actual: {exc!r}")
                return
            passed += 1
        except Exception as exc:
            print(f"FAIL  {name}\n  unexpected exception: {type(exc).__name__}: {exc!r}")
        else:
            print(f"FAIL  {name}\n  expected {kind.__name__}")

    with Mock115OpenServer() as server:
        state = server.state
        p115_open.PASSPORT = server.base
        p115_open.OPEN_API = server.base

        state.add_folder("dir-1", "待整理")
        state.add_file("file-1", "movie.mkv", parent="dir-1")

        expect("initial unauthorized", token_status()["authorized"], False)
        raises("no token", access_token, P115AuthError, "未授权")

        data = authorize_with_refresh_token("initial-token")
        set_setting("p115_auth_mode", "open")
        expect("authorized", token_status()["authorized"], True)
        expect("nickname", data.get("nickname"), "开放用户")
        expect("uid", data.get("uid"), 42)
        expect("one refresh", state.refresh_count, 1)
        expect("old refresh rotated", "initial-token" in state.refresh_tokens, False)
        expect("new refresh saved", "refresh-1" in state.refresh_tokens, True)

        first_access = str(data["access_token"])
        expect("cached access", access_token(), first_access)
        expect("cache avoids refresh", state.refresh_count, 1)

        status = refresh_now()
        expect("manual refresh rotates", state.refresh_count, 2)
        expect("refresh returns status", status.get("authorized"), True)
        second_access = access_token()
        expect("access changed", second_access != first_access, True)

        # 失败请求携带旧 token 时，若已有新 token 则直接复用，不再消耗 refresh_token
        expect(
            "stale token reuses fresh cache",
            access_token(force=True, stale=first_access),
            second_access,
        )
        expect("stale reuse no refresh", state.refresh_count, 2)

        # 并发强制刷新只应消耗一次 refresh_token
        state.refresh_delay = 0.15
        before = state.refresh_count
        barrier = threading.Barrier(5)
        results: list[str] = []

        def worker() -> None:
            barrier.wait()
            results.append(access_token(force=True))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        state.refresh_delay = 0.0
        expect("concurrent single refresh", state.refresh_count - before, 1)
        expect("concurrent same token", len(set(results)), 1)

        with P115OpenClient(qps=50) as client:
            info = client.test()
            expect("client nickname", info.get("nickname"), "开放用户")
            expect("client uid", info.get("uid"), 42)

            folder_id = client.ensure_folder_path(["待整理", "_订阅", "API"])
            expect("folder id", bool(folder_id), True)
            expect(
                "folder created",
                [entry["name"] for entry in client.list_subfolders(["待整理"])],
                ["_订阅"],
            )
            expect(
                "subfolder children",
                [entry["name"] for entry in client.list_subfolders(["待整理", "_订阅"])],
                ["API"],
            )
            entries = client.list_entries("dir-1")
            expect("file name", entries[0]["name"], "movie.mkv")
            expect("file is_dir", entries[0]["is_dir"], False)
            expect("file size", entries[0]["size"], 1024)

            magnet = "magnet:?xt=urn:btih:ABCDEF0123456789&dn=Test"
            task = client.add_task(magnet, folder_id=folder_id)
            expect("task id", task["id"], "btih:abcdef0123456789")
            expect("task folder", state.added_fields[0]["wp_path_id"][0], folder_id)
            expect("task urls field", state.added_fields[0]["urls"][0], magnet)

            tasks = client.list_tasks()
            expect("task downloading", tasks[0]["status"], "downloading")
            expect("task percent", tasks[0]["percent"], 50.0)
            tasks = client.list_tasks()
            expect("task done", tasks[0]["status"], "done")
            expect("task folder id", tasks[0]["folder_id"], folder_id)

            state.duplicate = True
            duplicate = client.add_task(magnet, folder_id=folder_id)
            expect("duplicate task", duplicate["id"], task_key(magnet))
            state.duplicate = False

            # access_token 失效：刷新后自动重试
            before = state.refresh_count
            state.expire_once = True
            names = [entry["name"] for entry in client.list_entries("dir-1")]
            expect("auth retry works", names, ["movie.mkv", "_订阅"])
            expect("auth retry refreshed", state.refresh_count - before, 1)

            # code 99 同样触发刷新重试
            before = state.refresh_count
            state.fail_99_once = True
            names = [entry["name"] for entry in client.list_entries("dir-1")]
            expect("code 99 retry works", names, ["movie.mkv", "_订阅"])
            expect("code 99 refreshed", state.refresh_count - before, 1)

            # 手动续期后旧 access 仍可用（客户端每次重新读取令牌）
            before = state.refresh_count
            refresh_now()
            expect("refresh_now rotates", state.refresh_count - before, 1)
            expect("client still works", client.test().get("ok"), True)

        # refresh_token 失效：标记需重新授权
        state.refresh_mode = "reauth"
        raises("reauth required", lambda: access_token(force=True), P115AuthError, "重新粘贴")
        expect("needs reauth flag", token_status()["needs_reauth"], True)
        raises("build blocks", build_p115_client, P115AuthError, "重新粘贴")
        state.refresh_mode = ""

        # 重新授权后恢复正常
        state.refresh_tokens.add("manual-token")
        authorize_with_refresh_token("manual-token")
        expect("reauthorized", token_status()["needs_reauth"], False)
        expect("reauthorized token", token_status()["authorized"], True)

        # 解除授权
        clear_token()
        expect("cleared", token_status()["authorized"], False)
        raises("build after clear", build_p115_client, P115AuthError, "未授权")

    # Cookie 模式仍然可用；未配置时报错
    set_setting("p115_auth_mode", "cookie")
    raises("cookie mode without cookie", build_p115_client, P115Error, "Cookie")
    set_setting("p115_auth_mode", "open")

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
