"""幽灵作品（源文件已被外部移走 / 删除）清理的回归测试。

覆盖：
- 目录里一个视频都没有时，重新扫描也会把旧条目标记为 missing；
- 整理失败（failed）的条目同样会被清理，不再残留作品计数；
- 海报墙 / 全部文件校验发现文件已不存在时，直接把条目标记为 missing；
- 源根目录临时不可读（网络 / 权限错误）时不误清条目；
- 手动条目从 missing 恢复后，重新扫描会恢复为已识别状态。

运行：cd backend && .venv/bin/python ../tests/test_ghost_cleanup.py
使用临时数据库与临时本地媒体，不会读写真实影视库。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cineflow-ghost-data-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="cineflow-ghost-media-")
os.environ["CINEFLOW_DATA_DIR"] = TEMP_DATA
os.environ["CINEFLOW_DEMO"] = "false"
os.environ["CINEFLOW_ADMIN_PASSWORD"] = "cineflow"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import dumps, execute, loads, now, query_one  # noqa: E402
from app.main import app  # noqa: E402
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402
from app.core.providers.base import FileSource, SourceError  # noqa: E402

import app.core.runner as runner_api  # noqa: E402


class BrokenSource(FileSource):
    type = "broken"

    def list(self, path: str = ""):
        raise SourceError("模拟网络故障")


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

    media = Path(MEDIA_ROOT)
    (media / "ghost").mkdir(parents=True)
    (media / "inbox" / "Show").mkdir(parents=True)
    real_file = media / "inbox" / "Show" / "Show.S01E05.mkv"
    real_file.write_bytes(b"video")
    (media / "transient").mkdir(parents=True)
    (media / "works").mkdir(parents=True)
    (media / "items").mkdir(parents=True)
    (media / "items" / "Keep.mkv").write_bytes(b"keep")

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cineflow"})
        expect("login", login.status_code, 200)
        settings = client.put("/api/settings", json={"values": {"min_size_mb": 0}})
        expect("settings", settings.status_code, 200)

        source_id = execute(
            "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("幽灵测试源", "local", dumps({"base_path": str(media)}), now(), now()),
        )

        def add_library(name: str, root: str) -> int:
            return execute(
                "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
                "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    name,
                    root,
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

        def add_item(
            library_id: int,
            source_path: str,
            status: str,
            *,
            match: dict | None = None,
            parsed: dict | None = None,
            manual: int = 0,
            match_key: str | None = None,
        ) -> int:
            return execute(
                "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, candidates, "
                "status, manual, match_key, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    library_id,
                    source_path,
                    source_path.rsplit("/", 1)[-1],
                    5,
                    0.0,
                    dumps(parsed or {}),
                    dumps(match) if match else None,
                    dumps([]),
                    status,
                    manual,
                    match_key,
                    now(),
                    now(),
                ),
            )

        def run_task(path: str, payload: dict | None = None) -> dict:
            created = client.post(path, json=payload or {})
            expect(f"submit {path}", created.status_code, 200)
            task_id = created.json()["task_id"]
            detail: dict = {}
            for _ in range(100):
                detail = client.get(f"/api/tasks/{task_id}").json()
                if detail.get("status") not in {"queued", "running"}:
                    break
                time.sleep(0.1)
            if detail.get("status") == "failed":
                print("TASK FAILED:", detail.get("error"))
                for entry in detail.get("logs", [])[-10:]:
                    print("LOG:", entry.get("level"), entry.get("message"))
            return detail

        def item_status(item_id: int):
            row = query_one("SELECT status, parsed FROM items WHERE id=?", (item_id,))
            return row["status"] if row else None

        # --- 1. 目录里没有任何视频：扫描也要清理旧条目（含 failed） ---
        lib_ghost = add_library("幽灵计数库", "ghost")
        ghost_failed = add_item(
            lib_ghost,
            "ghost/A.S01E01.mkv",
            "failed",
            match={"tmdb_id": 111, "media_type": "tv", "title": "幽灵剧"},
            parsed={"season": 1, "episode": 1, "media_type": "tv"},
            match_key="tv:111",
        )
        ghost_matched = add_item(
            lib_ghost,
            "ghost/B.S01E02.mkv",
            "matched",
            match={"tmdb_id": 111, "media_type": "tv", "title": "幽灵剧"},
            parsed={"season": 1, "episode": 2, "media_type": "tv"},
            match_key="tv:111",
        )
        ghost_manual = add_item(
            lib_ghost,
            "ghost/C.mkv",
            "matched",
            match={"tmdb_id": 112, "media_type": "movie", "title": "幽灵电影"},
            parsed={"media_type": "movie", "title": "幽灵电影"},
            manual=1,
            match_key="movie:112",
        )
        scan_ghost = run_task(f"/api/libraries/{lib_ghost}/scan")
        expect("ghost scan status", scan_ghost.get("status"), "success")
        expect("ghost scan found", scan_ghost.get("stats", {}).get("found"), 0)
        expect("ghost scan missing", scan_ghost.get("stats", {}).get("missing"), 3)
        expect("ghost failed cleaned", item_status(ghost_failed), "missing")
        expect("ghost matched cleaned", item_status(ghost_matched), "missing")
        expect("ghost manual cleaned", item_status(ghost_manual), "missing")
        library_payload = client.get(f"/api/libraries/{lib_ghost}").json()
        expect("ghost works count", library_payload["counts"]["works"], 0)

        # --- 2. 手动条目从 missing 恢复：扫描后重新变为已识别 ---
        lib_restore = add_library("恢复测试库", "inbox")
        restore_item = add_item(
            lib_restore,
            "inbox/Show/Show.S01E05.mkv",
            "missing",
            match={"tmdb_id": 900001, "media_type": "tv", "title": "回归剧"},
            parsed={"season": 2, "episode": 3, "media_type": "tv", "raw": "Show.S01E05.mkv", "title": "Show"},
            manual=1,
            match_key="tv:900001",
        )
        scan_restore = run_task(f"/api/libraries/{lib_restore}/scan")
        expect("restore scan status", scan_restore.get("status"), "success")
        row = query_one("SELECT status, parsed FROM items WHERE id=?", (restore_item,))
        expect("restore status", row["status"], "matched")
        restored = loads(row["parsed"], {}) or {}
        expect("restore season kept", restored.get("season"), 2)
        expect("restore episode kept", restored.get("episode"), 3)

        # --- 3. 源根临时不可读：不把条目误判为丢失 ---
        lib_transient = add_library("临时故障库", "transient")
        transient_item = add_item(
            lib_transient,
            "transient/X.mkv",
            "matched",
            match={"tmdb_id": 999, "media_type": "movie", "title": "故障电影"},
            parsed={"media_type": "movie"},
            match_key="movie:999",
        )
        original_build = runner_api.build_source
        runner_api.build_source = lambda _row: BrokenSource({})
        try:
            scan_transient = run_task(f"/api/libraries/{lib_transient}/scan")
        finally:
            runner_api.build_source = original_build
        expect("transient scan status", scan_transient.get("status"), "success")
        expect("transient not cleaned", item_status(transient_item), "matched")

        # --- 4. 海报墙校验发现文件不存在：直接标记 missing 并移出作品计数 ---
        lib_works = add_library("海报墙清理库", "works")
        works_missing = add_item(
            lib_works,
            "works/Ghost.Movie.mkv",
            "matched",
            match={"tmdb_id": 900, "media_type": "movie", "title": "幽灵电影"},
            parsed={"media_type": "movie"},
            match_key="movie:900",
        )
        works_gone_dir = add_item(
            lib_works,
            "works/Sub/Ghost2.mkv",
            "failed",
            match={"tmdb_id": 901, "media_type": "tv", "title": "幽灵剧"},
            parsed={"season": 1, "episode": 1, "media_type": "tv"},
            match_key="tv:901",
        )
        works_payload = client.get(f"/api/libraries/{lib_works}/works").json()
        expect("works hidden count", works_payload.get("hidden_missing"), 2)
        expect("works empty", works_payload.get("works"), [])
        expect("works first cleaned", item_status(works_missing), "missing")
        expect("works gone dir cleaned", item_status(works_gone_dir), "missing")
        works_counts = client.get(f"/api/libraries/{lib_works}").json()["counts"]
        expect("works count zero", works_counts["works"], 0)

        # --- 5. 「全部文件」校验发现文件不存在：同样清理 ---
        lib_items = add_library("全部文件清理库", "items")
        items_missing = add_item(
            lib_items,
            "items/A.mkv",
            "matched",
            match={"tmdb_id": 902, "media_type": "movie", "title": "甲"},
            parsed={"media_type": "movie"},
            match_key="movie:902",
        )
        items_keep = add_item(
            lib_items,
            "items/Keep.mkv",
            "matched",
            match={"tmdb_id": 903, "media_type": "movie", "title": "乙"},
            parsed={"media_type": "movie"},
            match_key="movie:903",
        )
        source_items = client.get(f"/api/libraries/{lib_items}/source-items").json()
        expect("source items total", source_items.get("total"), 1)
        expect("source items hidden", source_items.get("hidden_missing"), 1)
        expect("source items kept", [row["file_name"] for row in source_items.get("items", [])], ["Keep.mkv"])
        expect("source items cleaned", item_status(items_missing), "missing")
        expect("source items kept status", item_status(items_keep), "matched")

    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
