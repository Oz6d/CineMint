"""手动识别 → 重新扫描 → 整理 的回归测试。

覆盖：
- 重新扫描不会覆盖手动匹配写入的媒体类型 / 季集号；
- 扫描仍会刷新解析出的技术信息（如分辨率）；
- 手动匹配条目扫描后可以整理成功，不再因为没有季集号退回待确认；
- 手动编辑季集信息（PUT /api/items/{id}）会标记为手动，扫描同样保留；
- 手动匹配会记住标题，同一部剧的后续文件无需 TMDb 即可自动识别；
- 「重新自动匹配」会清除手动标记与学到的标题别名，回到自动识别。

运行：cd backend && .venv/bin/python ../tests/test_manual_scan.py
脚本使用临时数据目录与临时本地媒体，不会读写真实影视库。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cinemint-manual-scan-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="cinemint-manual-media-")
os.environ["CINEMINT_DATA_DIR"] = TEMP_DATA
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import dumps, execute, loads, now, query_one  # noqa: E402
from app.main import app  # noqa: E402
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402

import app.api.items as items_api  # noqa: E402
import app.core.runner as runner_api  # noqa: E402


class FakeTMDB:
    """离线假的 TMDb 客户端：返回固定的剧集 / 电影详情，不访问网络。"""

    configured = True

    def details(self, tmdb_id: int, media_type: str) -> dict:
        return {
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "title": "神秘剧集" if media_type == "tv" else "神秘电影",
            "original_title": "Mystery Show" if media_type == "tv" else "Mystery Movie",
            "year": 2020,
            "overview": "",
            "poster_path": "",
            "backdrop_path": "",
            "logo_path": "",
            "seasons": [],
        }

    def season(self, tv_id: int, season_number: int) -> dict:
        return {"season_number": season_number, "episodes": []}

    def search(self, query: str, media_type: str = "", year: int | None = None) -> list:
        return []


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

    fake = FakeTMDB()
    items_api.client_from_settings = lambda: fake
    runner_api.client_from_settings = lambda: fake

    media = Path(MEDIA_ROOT)
    show_dir = media / "inbox" / "神秘作品"
    show_dir.mkdir(parents=True)
    (show_dir / "Mystery.1080p.mkv").write_bytes(b"video-a")
    (show_dir / "Second.720p.mkv").write_bytes(b"video-b")
    (show_dir / "Third.2160p.mkv").write_bytes(b"video-c")
    (show_dir / "Show.S01E05.mkv").write_bytes(b"video-d")

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cinemint"})
        expect("login", login.status_code, 200)

        settings = client.put("/api/settings", json={"values": {"min_size_mb": 0}})
        expect("settings saved", settings.status_code, 200)
        expect("min size disabled", settings.json().get("min_size_mb"), 0)

        source_id = execute(
            "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("手动扫描测试源", "local", dumps({"base_path": str(media)}), now(), now()),
        )
        library_id = execute(
            "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
            "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "手动扫描测试库",
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
            return detail

        def item_id(source_path: str) -> int:
            row = query_one(
                "SELECT id FROM items WHERE library_id=? AND source_path=?",
                (library_id, source_path),
            )
            return int(row["id"]) if row else 0

        # 第一次扫描：四个文件都无法被自动识别（没有配置 TMDb），季集号为空。
        scan1 = run_task(f"/api/libraries/{library_id}/scan")
        expect("scan1 status", scan1.get("status"), "success")
        expect("scan1 found", scan1.get("stats", {}).get("unmatched"), 4)

        path_a = "inbox/神秘作品/Mystery.1080p.mkv"
        path_b = "inbox/神秘作品/Second.720p.mkv"
        path_c = "inbox/神秘作品/Third.2160p.mkv"
        path_d = "inbox/神秘作品/Show.S01E05.mkv"
        a_id, b_id, c_id, d_id = item_id(path_a), item_id(path_b), item_id(path_c), item_id(path_d)
        expect("item a created", a_id > 0, True)
        expect("item b created", b_id > 0, True)
        expect("item c created", c_id > 0, True)
        expect("item d created", d_id > 0, True)

        a = query_one("SELECT * FROM items WHERE id=?", (a_id,))
        a_parsed = loads(a["parsed"], {}) or {}
        expect("scan1 a unmatched", a["status"], "unmatched")
        expect("scan1 a no season", a_parsed.get("season"), None)
        expect("scan1 a resolution", a_parsed.get("resolution"), "1080p")

        # 手动把 A 识别为剧集（默认季集号 1/1 之外，明确指定 S02E03）。
        match_a = client.post(
            f"/api/items/{a_id}/match",
            json={"tmdb_id": 900001, "media_type": "tv", "season": 2, "episode": 3, "status": "matched"},
        )
        expect("manual match a", match_a.status_code, 200)
        body_a = match_a.json()
        expect("manual match a season", body_a["parsed"].get("season"), 2)
        expect("manual match a episode", body_a["parsed"].get("episode"), 3)
        expect("manual match a type", body_a["parsed"].get("media_type"), "tv")
        expect("manual match a flag", body_a.get("manual"), True)

        # 手动把 B 识别为电影。
        match_b = client.post(
            f"/api/items/{b_id}/match",
            json={"tmdb_id": 900002, "media_type": "movie", "status": "matched"},
        )
        expect("manual match b", match_b.status_code, 200)
        expect("manual match b flag", match_b.json().get("manual"), True)

        # 通过详情接口手动修改 C 的季集信息，也应视为手动识别。
        patch_c = client.put(
            f"/api/items/{c_id}",
            json={"season": 1, "episode": 8, "status": "review"},
        )
        expect("patch c", patch_c.status_code, 200)
        expect("patch c flag", patch_c.json().get("manual"), True)
        row_c = query_one("SELECT * FROM items WHERE id=?", (c_id,))
        expect("patch c stored manual", row_c["manual"], 1)

        # 手动把 D 识别为 S02E03：文件名里的 S01E05 不应覆盖手动值。
        match_d = client.post(
            f"/api/items/{d_id}/match",
            json={"tmdb_id": 900001, "media_type": "tv", "season": 2, "episode": 3, "status": "matched"},
        )
        expect("manual match d", match_d.status_code, 200)

        # 手动匹配会记住标题，同一部剧的新文件应能直接复用（无需 TMDb）。
        expect(
            "alias recorded",
            bool(query_one("SELECT id FROM match_aliases WHERE alias_key='mystery'")),
            True,
        )
        later_dir = media / "inbox" / "后续"
        later_dir.mkdir(parents=True)
        (later_dir / "Mystery [04].mkv").write_bytes(b"video-e")

        # 第二次扫描：手动识别信息必须保留，技术信息照常刷新。
        scan2 = run_task(f"/api/libraries/{library_id}/scan")
        expect("scan2 status", scan2.get("status"), "success")
        expect("scan2 kept manual", scan2.get("stats", {}).get("manual_kept"), 4)
        expect("scan2 alias matched", scan2.get("stats", {}).get("matched"), 1)

        a = query_one("SELECT * FROM items WHERE id=?", (a_id,))
        a_parsed = loads(a["parsed"], {}) or {}
        expect("a still manual", a["manual"], 1)
        expect("a status kept", a["status"], "matched")
        expect("a match key kept", a["match_key"], "tv:900001")
        expect("a season kept", a_parsed.get("season"), 2)
        expect("a episode kept", a_parsed.get("episode"), 3)
        expect("a media type kept", a_parsed.get("media_type"), "tv")
        expect("a resolution refreshed", a_parsed.get("resolution"), "1080p")

        b = query_one("SELECT * FROM items WHERE id=?", (b_id,))
        expect("b still manual", b["manual"], 1)
        expect("b status kept", b["status"], "matched")

        c = query_one("SELECT * FROM items WHERE id=?", (c_id,))
        c_parsed = loads(c["parsed"], {}) or {}
        expect("c still manual", c["manual"], 1)
        expect("c status kept", c["status"], "review")
        expect("c season kept", c_parsed.get("season"), 1)
        expect("c episode kept", c_parsed.get("episode"), 8)

        d = query_one("SELECT * FROM items WHERE id=?", (d_id,))
        d_parsed = loads(d["parsed"], {}) or {}
        expect("d season kept over filename", d_parsed.get("season"), 2)
        expect("d episode kept over filename", d_parsed.get("episode"), 3)

        later_id = item_id("inbox/后续/Mystery [04].mkv")
        later = query_one("SELECT * FROM items WHERE id=?", (later_id,))
        later_parsed = loads(later["parsed"], {}) or {}
        expect("alias item matched", later["status"], "matched")
        expect("alias item match key", later["match_key"], "tv:900001")
        expect("alias item season default", later_parsed.get("season"), 1)
        expect("alias item episode", later_parsed.get("episode"), 4)
        expect("alias item not manual", later["manual"], 0)

        # 整理 A：修复前这里会因为季集号被扫描清空而失败（剧集缺少季/集号）。
        organize = run_task(f"/api/libraries/{library_id}/organize", {"item_ids": [a_id], "scope": "all"})
        expect("organize status", organize.get("status"), "success")
        expect("organize success", organize.get("stats", {}).get("success"), 1)
        expect("organize failed", organize.get("stats", {}).get("failed"), 0)

        a = query_one("SELECT * FROM items WHERE id=?", (a_id,))
        expected_target = "library/神秘剧集 (2020)/Season 2/神秘剧集 S02E03.1080p.mkv"
        expect("a organized", a["status"], "organized")
        expect("a target path", a["target_path"], expected_target)
        expect("a target exists", (media / expected_target).exists(), True)
        expect("a source moved", (media / path_a).exists(), False)

        # 重新自动匹配：放弃手动结果，回到自动识别（手动标记清除）。
        rematch = client.post(f"/api/items/{b_id}/rematch")
        expect("rematch b", rematch.status_code, 200)
        b = query_one("SELECT * FROM items WHERE id=?", (b_id,))
        expect("b manual cleared", b["manual"], 0)
        expect("b match cleared", b["match"], None)
        expect("b status from auto", b["status"], "unmatched")
        expect(
            "alias cleared by rematch",
            query_one("SELECT id FROM match_aliases WHERE alias_key='second'"),
            None,
        )

    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
