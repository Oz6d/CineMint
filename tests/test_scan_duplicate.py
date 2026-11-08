"""扫描条目重复插入降级与影视库互斥锁回归测试。

运行：cd backend && .venv/bin/python ../tests/test_scan_duplicate.py
使用临时数据库与临时媒体目录，不读写真实库。
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DATA_DIR = tempfile.mkdtemp(prefix="cinemint-scan-dup-")
MEDIA_DIR = tempfile.mkdtemp(prefix="cinemint-scan-media-")
os.environ["CINEMINT_DATA_DIR"] = DATA_DIR
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from app import db  # noqa: E402

db.init_db()

import app.core.pipeline as pipeline  # noqa: E402
from app.core.library_guard import LIBRARY_LOCK_TIMEOUT, LibraryBusy, hold_library  # noqa: E402
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402
from app.db import dumps, execute, now, query_all, query_one, set_setting  # noqa: E402


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    media = Path(MEDIA_DIR)
    show = media / "inbox" / "Show"
    show.mkdir(parents=True)
    (show / "Show.S01E01.mkv").write_bytes(b"video")
    set_setting("min_size_mb", 0)

    source_id = execute(
        "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
        ("本地测试源", "local", dumps({"base_path": str(media)}), now(), now()),
    )
    library_id = execute(
        "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
        "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            "扫描重复测试库",
            "inbox",
            "library",
            "auto",
            dumps(DEFAULT_NAMING),
            dumps({**DEFAULT_SCRAPE, "enabled": False}),
            1,
            0,
            now(),
            now(),
        ),
    )

    # ---- 模拟并发扫描：另一个扫描先插入成功，本线程插入撞唯一约束 ----
    real_execute = pipeline.execute
    state = {"hit": False}

    def flaky_execute(sql: str, params: tuple = ()):
        if sql.lstrip().startswith("INSERT INTO items") and not state["hit"]:
            state["hit"] = True
            real_execute(sql, params)  # 对方扫描已经写入同一路径
            raise sqlite3.IntegrityError(
                "UNIQUE constraint failed: items.library_id, items.source_path"
            )
        return real_execute(sql, params)

    pipeline.execute = flaky_execute
    try:
        result = pipeline.scan_library(library_id)
    finally:
        pipeline.execute = real_execute

    expect("scan conflict insert happened", state["hit"], True)
    expect("scan conflict found files", result["total"], 1)
    expect("scan conflict returned item", len(result["items"]), 1)
    rows = query_all("SELECT * FROM items WHERE library_id=?", (library_id,))
    expect("scan conflict single row", len(rows), 1)
    expect("scan conflict row path", rows[0]["source_path"], "inbox/Show/Show.S01E01.mkv")
    expect("scan conflict row status", rows[0]["status"] in {"matched", "review", "unmatched"}, True)

    # ---- 互斥锁：同一影视库的并发操作必须串行 ----
    active = {"now": 0, "peak": 0}
    guard = threading.Lock()

    def worker() -> None:
        with hold_library(9001, timeout=5):
            with guard:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.1)
            with guard:
                active["now"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    expect("library lock serialized", active["peak"], 1)

    # ---- 锁被占用超过等待时间时抛 LibraryBusy，而不是无限等待 ----
    holding = threading.Event()

    def holder() -> None:
        with hold_library(9002, timeout=1):
            holding.set()
            time.sleep(0.5)

    holder_thread = threading.Thread(target=holder)
    holder_thread.start()
    holding.wait(timeout=2)
    outcome = []

    def contender() -> None:
        try:
            with hold_library(9002, timeout=0.05):
                outcome.append("acquired")
        except LibraryBusy:
            outcome.append("busy")

    contender_thread = threading.Thread(target=contender)
    contender_thread.start()
    contender_thread.join()
    holder_thread.join()
    expect("library lock busy timeout", outcome, ["busy"])
    expect("library lock default timeout", LIBRARY_LOCK_TIMEOUT >= 300, True)

    # ---- 无竞争时可以重新获取 ----
    with hold_library(9002, timeout=0.2):
        pass
    expect("library lock reusable", True, True)

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
