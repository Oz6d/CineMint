"""夸克订阅：未识别文件手动匹配后，下一集能否自动识别并整理。

场景（用户反馈）：
1. 订阅首次转存，文件名是罗马音 / 无法自动识别，条目标记为 unmatched；
2. 用户在界面对该文件手动匹配（POST /api/items/{id}/match，会学习标题别名）；
3. 分享里出现下一集，订阅再次运行时应通过学到的标题自动匹配，
   并在订阅开启「自动整理」时直接把新集整理到目标目录。

运行：cd backend && .venv/bin/python ../tests/test_manual_match_subscription.py
使用临时数据库、临时媒体与本地假夸克接口，不访问网络。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEMP_DATA = tempfile.mkdtemp(prefix="cinemint-manual-sub-")
os.environ["CINEMINT_DATA_DIR"] = TEMP_DATA
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from fastapi.testclient import TestClient  # noqa: E402

from quark_mock import MockQuarkServer  # noqa: E402

import app.api.items as items_api  # noqa: E402
import app.core.pipeline as pipeline  # noqa: E402
import app.core.subscriptions as subscriptions  # noqa: E402
from app.core.naming import DEFAULT_NAMING  # noqa: E402
from app.core.quark import QuarkClient  # noqa: E402
from app.core.subscription_runs import create_run, finish_run, start_run  # noqa: E402
from app.db import (  # noqa: E402
    dumps,
    execute,
    init_db,
    now,
    query_all,
    query_one,
    set_setting,
)
from app.main import app  # noqa: E402

SHOW = {
    "tmdb_id": 900001,
    "media_type": "tv",
    "title": "鼠标光标现实操作",
    "original_title": "Mouse Cursor de Genjitsu",
    "year": 2026,
    "poster_path": "",
    "backdrop_path": "",
    "logo_path": "",
    "overview": "",
    "genres": [],
    "seasons": [],
}

EP1 = "[SubGroup] Mouse Cursor de Genjitsu - 01.mkv"
EP2 = "[SubGroup] Mouse Cursor de Genjitsu - 02.mkv"


class FakeTMDB:
    """离线假 TMDb：只给手动匹配接口返回详情，不做在线搜索。"""

    configured = True

    def details(self, tmdb_id: int, media_type: str) -> dict:
        return dict(SHOW, tmdb_id=tmdb_id, media_type=media_type)

    def season(self, tv_id: int, season_number: int) -> dict:
        return {"season_number": season_number, "episodes": []}

    def search(self, query: str, media_type: str = "", year: int | None = None) -> list:
        return []


def fake_match(
    parsed, folder, client, *, auto_threshold=0.72, review_threshold=0.5, folder_path=""
):
    """模拟罗马音标题在 TMDb 搜不到：自动匹配一律未识别。"""
    return {
        "candidates": [],
        "best": None,
        "status": "unmatched",
        "source": "test",
        "error": "测试：TMDb 没有找到匹配",
    }


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

    media = Path(tempfile.mkdtemp(prefix="cinemint-manual-sub-media-"))
    inbox = media / "待整理"
    target = media / "For infuse"
    inbox.mkdir()
    target.mkdir()

    init_db()
    fake = FakeTMDB()
    items_api.client_from_settings = lambda: fake
    pipeline.match_item = fake_match
    set_setting("tmdb_api_key", "test-key")
    set_setting("min_size_mb", 0)
    set_setting("max_files_per_scan", 3000)

    with MockQuarkServer() as server, TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cinemint"})
        expect("login", login.status_code, 200)

        server.state.save_root = inbox
        ep1_fid = server.state.add_file(EP1, fid="ep-1")
        subscriptions.quark_client = lambda: QuarkClient(
            "ck", base_url=server.url, qps=0
        )
        subscriptions.send_event = lambda *args, **kwargs: {"ok": True, "results": []}

        source_id = execute(
            "INSERT INTO sources(name,type,config,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("订阅手动匹配源", "local", dumps({"base_path": str(media)}), now(), now()),
        )
        naming = {
            **DEFAULT_NAMING,
            "auto_category": False,
            "episode_title": False,
        }
        library_id = execute(
            "INSERT INTO libraries(source_id,name,root_path,target_root,media_type,"
            "naming,scrape,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "订阅手动匹配库",
                "待整理",
                "For infuse",
                "tv",
                dumps(naming),
                dumps({"enabled": False}),
                now(),
                now(),
            ),
        )
        # enabled=0：测试里直接调用执行器，避免调度器自动领取后台执行。
        subscription_id = execute(
            "INSERT INTO subscriptions(name,type,enabled,library_id,config,schedule,"
            "auto_organize,replace_versions,notify_channel_ids,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "罗马音订阅",
                "quark",
                0,
                library_id,
                dumps(
                    {
                        "share_url": "https://pan.quark.cn/s/abc123def456",
                        "max_per_run": 20,
                        "visibility_timeout": 10,
                    }
                ),
                dumps({"mode": "interval", "interval_minutes": 60}),
                1,
                1,
                "[]",
                now(),
                now(),
            ),
        )

        def run_once() -> tuple[int, dict]:
            run_id = create_run(subscription_id, trigger="manual")
            start_run(run_id)
            row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
            result = subscriptions.run_subscription(row, run_id)
            finish_run(
                run_id,
                result["status"],
                stats=result["stats"],
                message=result.get("message") or "",
            )
            return run_id, result

        # ---------- 第一次：转存但无法识别 ----------
        _run_id, result = run_once()
        expect("run1 status", result["status"], "success")
        expect("run1 unmatched", result["stats"].get("unmatched"), 1)
        expect("run1 organized", result["stats"].get("organized"), 0)
        expect("run1 staged file kept", (inbox / EP1).exists(), True)
        entry1 = query_one(
            "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_key=?",
            (subscription_id, ep1_fid),
        )
        expect("run1 entry unmatched", entry1["status"], "unmatched")
        item1 = query_one(
            "SELECT * FROM items WHERE library_id=? AND source_path=?",
            (library_id, f"待整理/{EP1}"),
        )
        expect("run1 item unmatched", item1["status"], "unmatched")
        expect("run1 item not manual", item1["manual"], 0)
        expect("run1 target empty", list(target.rglob("*.mkv")), [])

        # ---------- 用户在界面手动匹配 ----------
        manual = client.post(
            f"/api/items/{item1['id']}/match",
            json={
                "tmdb_id": 900001,
                "media_type": "tv",
                "season": 1,
                "episode": 1,
                "status": "matched",
            },
        )
        expect("manual match status", manual.status_code, 200)
        expect("manual match flag", manual.json().get("manual"), True)
        alias = query_one(
            "SELECT * FROM match_aliases WHERE alias_key=?",
            ("mousecursordegenjitsu",),
        )
        expect("alias learned", bool(alias), True)
        expect("alias tmdb", int(alias["tmdb_id"]) if alias else 0, 900001)

        # ---------- 分享出现下一集，再次运行订阅 ----------
        ep2_fid = server.state.add_file(EP2, fid="ep-2")
        _run_id, result = run_once()
        expect("run2 status", result["status"], "success")
        expect("run2 adopted", result["stats"].get("adopted"), 1)
        expect("run2 transferred", result["stats"].get("transferred"), 1)
        expect("run2 organized", result["stats"].get("organized"), 2)
        expect("run2 failed", result["stats"].get("failed"), 0)

        entry2 = query_one(
            "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_key=?",
            (subscription_id, ep2_fid),
        )
        expect("run2 entry organized", entry2["status"], "organized")
        item2 = query_one(
            "SELECT * FROM items WHERE library_id=? AND file_name=?",
            (library_id, EP2),
        )
        item2_parsed = json.loads(item2["parsed"] or "{}")
        expect("run2 item matched", item2["status"], "organized")
        expect("run2 item not manual", item2["manual"], 0)
        expect("run2 item match key", item2["match_key"], "tv:900001")
        expect("run2 item season", item2_parsed.get("season"), 1)
        expect("run2 item episode", item2_parsed.get("episode"), 2)
        expect("run2 staging empty", list(inbox.rglob("*.mkv")), [])
        moved = sorted(path.name for path in target.rglob("*.mkv"))
        expect("run2 target two eps", len(moved), 2)
        expect("run2 target ep1", any("S01E01" in name for name in moved), True)
        expect("run2 target ep2", any("S01E02" in name for name in moved), True)

        organized_items = query_all(
            "SELECT * FROM items WHERE library_id=? AND status='organized' ORDER BY episode",
            (library_id,),
        )
        expect("run2 organized items", len(organized_items), 2)
        expect(
            "run2 organized episodes",
            [row["episode"] for row in organized_items],
            [1, 2],
        )
        expect(
            "run2 manual item kept",
            next(row["manual"] for row in organized_items if row["episode"] == 1),
            1,
        )

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
