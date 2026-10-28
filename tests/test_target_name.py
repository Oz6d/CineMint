"""整理后名称手动覆盖的回归测试。

覆盖：
- PUT /api/items/{id} 保存 / 清空 target_name；
- 文件详情返回 target_name_override 与自动 planned_name；
- 整理预览 / 仅重命名预览使用手动名称。

运行：cd backend && .venv/bin/python ../tests/test_target_name.py
使用临时数据库与临时媒体，不访问真实库。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEMP_DATA = tempfile.mkdtemp(prefix="cineflow-target-name-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="cineflow-target-name-media-")
os.environ["CINEFLOW_DATA_DIR"] = TEMP_DATA
os.environ["CINEFLOW_DEMO"] = "false"
os.environ["CINEFLOW_ADMIN_PASSWORD"] = "cineflow"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402
from app.db import dumps, execute, now  # noqa: E402
from app.main import app  # noqa: E402


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    passed = total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    media = Path(MEDIA_ROOT)
    inbox = media / "inbox"
    target = media / "library"
    inbox.mkdir(parents=True)
    target.mkdir(parents=True)
    (inbox / "Movie.2024.1080p.mkv").write_bytes(b"video")

    with TestClient(app) as client:
        expect("login", client.post("/api/auth/login", json={"password": "cineflow"}).status_code, 200)
        source_id = execute(
            "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("名称测试源", "local", dumps({"base_path": str(media)}), now(), now()),
        )
        library_id = execute(
            "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
            "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "名称测试库",
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
        item_id = execute(
            "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, status, "
            "match_key, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                library_id,
                "inbox/Movie.2024.1080p.mkv",
                "Movie.2024.1080p.mkv",
                10,
                0,
                dumps({"title": "Movie", "year": 2024, "media_type": "movie", "resolution": "1080p"}),
                dumps({"tmdb_id": 100, "media_type": "movie", "title": "Movie", "year": 2024}),
                "matched",
                "movie:100",
                now(),
                now(),
            ),
        )

        detail = client.get(f"/api/items/{item_id}")
        expect("detail status", detail.status_code, 200)
        expect("auto planned name", detail.json().get("planned_name"), "Movie (2024) 1080p.mkv")
        expect("override initially empty", detail.json().get("target_name_override"), "")

        saved = client.put(f"/api/items/{item_id}", json={"target_name": "Custom Movie.mkv"})
        expect("save override status", saved.status_code, 200)
        expect("save override value", saved.json().get("target_name_override"), "Custom Movie.mkv")
        expect("planned name remains automatic", saved.json().get("planned_name"), "Movie (2024) 1080p.mkv")

        listed = client.get(f"/api/libraries/{library_id}/items?status=matched")
        expect("list status", listed.status_code, 200)
        listed_item = next(
            (entry for entry in listed.json().get("items", []) if entry.get("id") == item_id),
            {},
        )
        expect("list planned name uses override", listed_item.get("planned_name"), "Custom Movie.mkv")
        expect("list auto name", listed_item.get("auto_name"), "Movie (2024) 1080p.mkv")
        expect("list override", listed_item.get("target_name_override"), "Custom Movie.mkv")

        plan = client.post(f"/api/libraries/{library_id}/plan", json={"item_ids": [item_id]})
        expect("plan status", plan.status_code, 200)
        planned = (plan.json().get("items") or [{}])[0]
        expect("plan target uses override", planned.get("target_path"), "library/Movie (2024)/Custom Movie.mkv")

        rename_plan = client.post(
            f"/api/libraries/{library_id}/rename-plan",
            json={"item_ids": [item_id], "rename_files": True, "rename_folders": False},
        )
        expect("rename plan status", rename_plan.status_code, 200)
        rename_item = (rename_plan.json().get("items") or [{}])[0]
        expect("rename plan uses override", rename_item.get("desired_file_name"), "Custom Movie.mkv")

        cleared = client.put(f"/api/items/{item_id}", json={"target_name": ""})
        expect("clear override status", cleared.status_code, 200)
        expect("clear override value", cleared.json().get("target_name_override"), "")

        bad = client.put(f"/api/items/{item_id}", json={"target_name": "../escape.mkv"})
        expect("reject path in override", bad.status_code, 400)

    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
