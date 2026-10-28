"""仅重命名 API / 任务 / 数据库路径同步的回归测试。

运行：cd backend && CINEMINT_DATA_DIR=$(mktemp -d) .venv/bin/python ../tests/test_rename_api.py
脚本内部会创建临时数据目录，不会使用 data/cinemint.db。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cinemint-rename-api-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="cinemint-rename-media-")
os.environ["CINEMINT_DATA_DIR"] = TEMP_DATA
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import dumps, execute, now, query_one  # noqa: E402
from app.main import app  # noqa: E402
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402


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
    show_dir = media / "inbox" / "喜剧之王" / "abc"
    show_dir.mkdir(parents=True)
    (show_dir / "老友记S01E01.mkv").write_bytes(b"video")
    (show_dir / "老友记S01E01.chs.ass").write_text("subtitle")
    (show_dir / "老友记S01E02.mkv").write_bytes(b"video2")

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cinemint"})
        expect("login", login.status_code, 200)

        source_id = execute(
            "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("测试文件源", "local", dumps({"base_path": str(media)}), now(), now()),
        )
        library_id = execute(
            "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
            "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "仅重命名测试库",
                "inbox",
                "library",
                "auto",
                dumps(DEFAULT_NAMING),
                dumps(DEFAULT_SCRAPE),
                1,
                0,
                now(),
                now(),
            ),
        )
        match = {
            "tmdb_id": 1668,
            "media_type": "tv",
            "title": "老友记",
            "original_title": "Friends",
            "year": 1994,
        }
        first_id = execute(
            "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, status, "
            "match_key, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                library_id,
                "inbox/喜剧之王/abc/老友记S01E01.mkv",
                "老友记S01E01.mkv",
                5,
                0,
                dumps({"title": "老友记", "year": 1994, "season": 1, "episode": 1, "media_type": "tv", "raw": "老友记S01E01.mkv"}),
                dumps(match),
                "matched",
                "tv:1668",
                now(),
                now(),
            ),
        )
        second_id = execute(
            "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, status, "
            "match_key, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                library_id,
                "inbox/喜剧之王/abc/老友记S01E02.mkv",
                "老友记S01E02.mkv",
                6,
                0,
                dumps({"title": "老友记", "year": 1994, "season": 1, "episode": 2, "media_type": "tv", "raw": "老友记S01E02.mkv"}),
                dumps(match),
                "matched",
                "tv:1668",
                now(),
                now(),
            ),
        )

        plan = client.post(
            f"/api/libraries/{library_id}/rename-plan",
            json={"item_ids": [first_id], "rename_files": True, "rename_folders": True},
        )
        expect("plan status", plan.status_code, 200)
        plan_item = (plan.json().get("items") or [{}])[0]
        expect(
            "plan target",
            plan_item.get("target_path"),
            "inbox/老友记 (1994)/Season 1/老友记 S01E01.mkv",
        )
        expect("plan folder changed", plan_item.get("folder_changed"), True)
        expect("plan file changed", plan_item.get("file_changed"), True)

        both_off = client.post(
            f"/api/libraries/{library_id}/rename",
            json={"item_ids": [first_id], "rename_files": False, "rename_folders": False},
        )
        expect("both options off rejected", both_off.status_code, 400)

        task = client.post(
            f"/api/libraries/{library_id}/rename",
            json={"item_ids": [first_id], "rename_files": True, "rename_folders": True},
        )
        expect("rename submit", task.status_code, 200)
        task_id = task.json()["task_id"]
        detail = None
        for _ in range(50):
            detail = client.get(f"/api/tasks/{task_id}").json()
            if detail.get("status") not in {"queued", "running"}:
                break
            time.sleep(0.1)
        expect("task status", (detail or {}).get("status"), "success")
        expect("task files", (detail or {}).get("stats", {}).get("files"), 1)
        expect("task folders", (detail or {}).get("stats", {}).get("folders"), 2)

        expect(
            "video renamed",
            (media / "inbox" / "老友记 (1994)" / "Season 1" / "老友记 S01E01.mkv").exists(),
            True,
        )
        expect(
            "subtitle renamed",
            (media / "inbox" / "老友记 (1994)" / "Season 1" / "老友记 S01E01.chs.ass").exists(),
            True,
        )
        first = query_one("SELECT * FROM items WHERE id=?", (first_id,))
        expect("item path updated", first["source_path"], "inbox/老友记 (1994)/Season 1/老友记 S01E01.mkv")
        expect("item name updated", first["file_name"], "老友记 S01E01.mkv")
        expect("item status kept", first["status"], "matched")

        second = query_one("SELECT * FROM items WHERE id=?", (second_id,))
        expect(
            "unselected sibling path updated",
            second["source_path"],
            "inbox/老友记 (1994)/Season 1/老友记S01E02.mkv",
        )
        expect(
            "unselected sibling file moved with folder",
            (media / "inbox" / "老友记 (1994)" / "Season 1" / "老友记S01E02.mkv").exists(),
            True,
        )

    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
