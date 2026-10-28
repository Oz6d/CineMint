"""夸克订阅端到端测试：转存 → 扫描 → 整理 → V2 替换 / 重复上报。

运行：cd backend && .venv/bin/python ../tests/test_subscriptions.py
使用本地假夸克接口、临时媒体目录和临时数据库，不访问真实网盘 / TMDb。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["CINEMINT_DATA_DIR"] = tempfile.mkdtemp(prefix="cinemint-subscriptions-")
os.environ["CINEMINT_DEMO"] = "false"

from quark_mock import MockQuarkServer  # noqa: E402

import app.core.pipeline as pipeline  # noqa: E402
import app.core.subscriptions as subscriptions  # noqa: E402
from app.core.metadata.tmdb import TMDBClient  # noqa: E402
from app.core.naming import DEFAULT_NAMING  # noqa: E402
from app.core.quark import QuarkClient  # noqa: E402
from app.core.subscription_runs import create_run, finish_run, start_run  # noqa: E402
from app.db import (  # noqa: E402
    dumps,
    execute,
    init_db,
    loads,
    now,
    query_all,
    query_one,
    set_setting,
)

SHOW = {
    "id": 123,
    "tmdb_id": 123,
    "media_type": "tv",
    "title": "Test Show",
    "name": "Test Show",
    "original_title": "Test Show",
    "year": 2024,
    "poster_path": "",
    "backdrop_path": "",
    "logo_path": "",
    "overview": "",
    "genres": [],
    "origin_country": ["JP"],
}


def fake_match(parsed, folder, client, *, auto_threshold=0.72, review_threshold=0.5):
    raw = parsed.raw or ""
    if "Mystery" in raw:
        return {
            "candidates": [],
            "best": None,
            "status": "unmatched",
            "source": "test",
            "error": "测试：没有找到匹配",
        }
    best = dict(SHOW, score=0.99)
    if "Review" in raw:
        return {
            "candidates": [best],
            "best": best,
            "status": "review",
            "source": "test",
            "confidence": 0.61,
        }
    return {
        "candidates": [best],
        "best": best,
        "status": "matched",
        "source": "test",
        "confidence": 0.99,
    }


def fake_details(self, tmdb_id: int, media_type: str):
    return dict(SHOW)


def fake_season(self, tv_id: int, season_number: int):
    return {
        "episodes": [
            {"episode_number": number, "name": f"Episode {number}"}
            for number in range(1, 25)
        ]
    }


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

    media = Path(tempfile.mkdtemp(prefix="cinemint-sub-media-"))
    (media / "待整理").mkdir()
    (media / "For infuse").mkdir()
    inbox = media / "待整理"
    target = media / "For infuse"

    set_setting("tmdb_api_key", "test-key")
    set_setting("min_size_mb", 0)
    set_setting("max_files_per_scan", 3000)

    source_id = execute(
        "INSERT INTO sources(name,type,config,created_at,updated_at) VALUES(?,?,?,?,?)",
        ("本地测试", "local", dumps({"base_path": str(media)}), now(), now()),
    )
    naming = {
        **DEFAULT_NAMING,
        "auto_category": False,
        "episode_title": False,
        "episode_file": "{title} S{season:02d}E{episode:02d}",
    }
    library_id = execute(
        "INSERT INTO libraries(source_id,name,root_path,target_root,media_type,naming,scrape,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            "测试库",
            "待整理",
            "For infuse",
            "tv",
            dumps(naming),
            dumps({"enabled": False}),
            now(),
            now(),
        ),
    )

    e01 = "[DKB] Dragon Ball Super Beerus - S01E01 [1080p][V2].mkv"
    e02 = "[DKB] Dragon Ball Super Beerus - S01E02 [1080p][HEVC x265 10bit].mkv"
    e02_v2 = "[DKB] Dragon Ball Super Beerus - S01E02 [1080p][V2].mkv"
    e03 = "[DKB] Dragon Ball Super Beerus - S01E03 [1080p].mkv"
    e03_dup = "[DKB] Dragon Ball Super Beerus - S01E03 [720p].mkv"

    sent: list[dict] = []

    def fake_send(event, title, content, *, channel_ids=None, match_events=None):
        sent.append(
            {
                "event": event,
                "title": title,
                "content": content,
                "match_events": match_events or [],
            }
        )
        return {"ok": True, "results": []}

    def summary_text() -> str:
        return "\n".join(
            entry["content"] for entry in sent if entry["event"] == "run.summary"
        )

    with MockQuarkServer() as server:
        # 转存目录留空 = 影视库根目录，文件直接落到待整理目录。
        server.state.save_root = inbox
        season_fid = server.state.add_folder("Season 1", fid="season-1")
        server.state.add_file(e01, parent=season_fid, fid="f1")
        server.state.add_file(e02, parent=season_fid, fid="f2")
        server.state.add_file("readme.txt", parent=season_fid, fid="f3")
        subscription_id = execute(
            "INSERT INTO subscriptions(name,type,enabled,library_id,config,schedule,"
            "auto_organize,replace_versions,notify_channel_ids,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "Test",
                "quark",
                1,
                library_id,
                dumps(
                    {
                        "share_url": "https://pan.quark.cn/s/abc123def456",
                        "keyword": "Dragon Ball Super Beerus",
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

        library_row = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
        subscription_row = query_one(
            "SELECT * FROM subscriptions WHERE id=?", (subscription_id,)
        )
        blank_config = subscriptions.normalize_config(
            loads(subscription_row["config"], {}), subscription_row["name"]
        )
        expect("blank config save root", blank_config["save_root"], True)
        expect("blank config save subdir", blank_config["save_subdir"], "")
        expect(
            "blank config staging root",
            subscriptions._staging_for(
                library_row, blank_config, subscription_row
            )[0],
            "待整理",
        )

        pipeline.match_item = fake_match
        TMDBClient.details = fake_details
        TMDBClient.season = fake_season
        subscriptions.quark_client = lambda: QuarkClient(
            "ck", base_url=server.url, qps=0
        )
        subscriptions.send_event = fake_send

        def run_once():
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

        # ---------- 第一次：转存并整理两集 ----------
        _run_id, result = run_once()
        expect("run1 status", result["status"], "success")
        expect("run1 transferred", result["stats"].get("transferred"), 2)
        expect("run1 organized", result["stats"].get("organized"), 2)
        expect("run1 entries", len(query_all(
            "SELECT * FROM subscription_entries WHERE subscription_id=? AND status='organized'",
            (subscription_id,),
        )), 2)
        target_files = sorted(
            path.name for path in target.rglob("*.mkv")
        )
        expect("run1 target files", target_files, [
            "Test Show S01E01.mkv",
            "Test Show S01E02.mkv",
        ])
        organized = query_all(
            "SELECT * FROM items WHERE library_id=? AND status='organized' ORDER BY episode",
            (library_id,),
        )
        expect("run1 item episodes", [row["episode"] for row in organized], [1, 2])
        expect("run1 item versions", [row["version"] for row in organized], [2, 0])
        expect("run1 no notification duplicate", "【重复" in summary_text(), False)
        expect("run1 summary notified", any(
            entry["event"] == "run.summary" for entry in sent
        ), True)
        expect("run1 single aggregated push", len(sent), 1)
        expect("run1 transfer section", "【转存完成 · 2】" in summary_text(), True)
        expect("run1 organize section", "【整理完成 · 2】" in summary_text(), True)
        e02_target = next(
            path for path in target.rglob("*S01E02*.mkv")
        )
        expect("run1 E02 content", e02_target.read_bytes(), b"content:f2")

        # ---------- 第二次：只转存新增集 ----------
        sent.clear()
        server.state.add_file(e03, parent=season_fid, fid="f4")
        _run_id, result = run_once()
        expect("run2 transferred only new", result["stats"].get("transferred"), 1)
        expect("run2 organized", result["stats"].get("organized"), 1)
        expect("run2 target count", len(list(target.rglob("*.mkv"))), 3)

        # ---------- 第三次：普通重复只上报 ----------
        sent.clear()
        server.state.add_file(e03_dup, parent=season_fid, fid="f5")
        _run_id, result = run_once()
        expect("run3 duplicate", result["stats"].get("duplicate"), 1)
        expect("run3 duplicate notification", "【重复 · 1】" in summary_text(), True)
        expect("run3 duplicate file listed", e03_dup in summary_text(), True)
        expect("run3 single aggregated push", len(sent), 1)
        expect("run3 target untouched", len(list(target.rglob("*.mkv"))), 3)
        duplicate_entry = query_one(
            "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_key='f5'",
            (subscription_id,),
        )
        expect("run3 duplicate entry status", duplicate_entry["status"], "duplicate")

        # ---------- 第四次：V2 替换已整理的同一集 ----------
        sent.clear()
        server.state.add_file(e02_v2, parent=season_fid, fid="f6")
        _run_id, result = run_once()
        expect("run4 replaced", result["stats"].get("replaced"), 1)
        expect("run4 organized", result["stats"].get("organized"), 1)
        expect("run4 replaced notified", "【V2 替换 · 1】" in summary_text(), True)
        expect("run4 replaced file listed", e02_v2 in summary_text(), True)
        expect(
            "run4 old version listed",
            "Test Show S01E02" in summary_text(),
            True,
        )
        expect("run4 single aggregated push", len(sent), 1)
        e02_files = list(target.rglob("*S01E02*.mkv"))
        expect("run4 one E02 file", len(e02_files), 1)
        expect("run4 E02 content", e02_files[0].read_bytes(), b"content:f6")
        e02_items = query_all(
            "SELECT * FROM items WHERE library_id=? AND season=1 AND episode=2",
            (library_id,),
        )
        expect("run4 old item removed", len(e02_items), 1)
        expect("run4 new item version", e02_items[0]["version"], 2)
        expect("run4 new item organized", e02_items[0]["status"], "organized")
        trash = target / ".cinemint-trash"
        expect("run4 trash cleaned", not trash.exists() or not list(trash.rglob("*")), True)

        # ---------- 第五次：未识别 / 待确认 ----------
        sent.clear()
        execute(
            "UPDATE subscriptions SET config=? WHERE id=?",
            (
                dumps(
                    {
                        "share_url": "https://pan.quark.cn/s/abc123def456",
                        "max_per_run": 20,
                        "visibility_timeout": 10,
                    }
                ),
                subscription_id,
            ),
        )
        server.state.add_file("Mystery.Show.S01E01.mkv", parent=season_fid, fid="f7")
        server.state.add_file("Mystery.Show.S01E02.mkv", parent=season_fid, fid="f7b")
        server.state.add_file("Mystery.Show.S01E03.mkv", parent=season_fid, fid="f7c")
        server.state.add_file("Review.Show.S01E01.mkv", parent=season_fid, fid="f8")
        _run_id, result = run_once()
        expect("run5 unmatched", result["stats"].get("unmatched"), 3)
        expect("run5 review", result["stats"].get("review"), 1)
        expect("run5 unmatched aggregated", "【无法识别 · 3】" in summary_text(), True)
        expect(
            "run5 all unmatched listed",
            all(f"Mystery.Show.S01E0{i}.mkv" in summary_text() for i in (1, 2, 3)),
            True,
        )
        expect("run5 review aggregated", "【待确认 · 1】" in summary_text(), True)
        expect("run5 single aggregated push", len(sent), 1)
        expect(
            "run5 match events",
            set(sent[0]["match_events"]) >= {"item.unmatched", "item.review"},
            True,
        )
        unmatched_entry = query_one(
            "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_key='f7'",
            (subscription_id,),
        )
        review_entry = query_one(
            "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_key='f8'",
            (subscription_id,),
        )
        expect("run5 unmatched entry", unmatched_entry["status"], "unmatched")
        expect("run5 review entry", review_entry["status"], "review")

        runs = query_all(
            "SELECT * FROM subscription_runs WHERE subscription_id=? ORDER BY id",
            (subscription_id,),
        )
        expect("run records", len(runs), 5)
        expect("run records finished", all(row["finished_at"] for row in runs), True)
        expect("run progress", runs[-1]["progress"], 100)
        logs = query_all(
            "SELECT * FROM subscription_run_logs WHERE run_id=? ORDER BY id",
            (runs[0]["id"],),
        )
        expect("run logs not empty", len(logs) > 0, True)

        # ---------- 第六次：使用选定的夸克目录 ID ----------
        picked = inbox / "Picked"
        picked.mkdir(parents=True, exist_ok=True)
        server.state.save_root = picked
        server.state.add_folder("Picked", my_drive=True, fid="picked-1")
        picked_subscription = execute(
            "INSERT INTO subscriptions(name,type,enabled,library_id,config,schedule,"
            "auto_organize,replace_versions,notify_channel_ids,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "Picked",
                "quark",
                1,
                library_id,
                dumps(
                    {
                        "share_url": "https://pan.quark.cn/s/abc123def456",
                        "keyword": "S01E05",
                        "save_subdir": "Picked",
                        "quark_folder_id": "picked-1",
                        "quark_folder_path": "Picked",
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
        e05 = "[DKB] Dragon Ball Super Beerus - S01E05 [1080p].mkv"
        server.state.add_file(e05, parent=season_fid, fid="f9")
        create_requests_before = sum(
            1
            for entry in server.state.requests
            if entry[0] == "POST" and entry[1] == "file"
        )
        picked_run = create_run(picked_subscription, trigger="manual")
        start_run(picked_run)
        picked_row = query_one(
            "SELECT * FROM subscriptions WHERE id=?", (picked_subscription,)
        )
        picked_result = subscriptions.run_subscription(picked_row, picked_run)
        finish_run(
            picked_run,
            picked_result["status"],
            stats=picked_result["stats"],
            message=picked_result.get("message") or "",
        )
        expect("run6 picked status", picked_result["status"], "success")
        expect("run6 picked organized", picked_result["stats"].get("organized"), 1)
        expect(
            "run6 picked target",
            [path.name for path in target.rglob("*S01E05*.mkv")],
            ["Test Show S01E05.mkv"],
        )
        expect(
            "run6 no folder creation",
            sum(
                1
                for entry in server.state.requests
                if entry[0] == "POST" and entry[1] == "file"
            ),
            create_requests_before,
        )
        sort_requests = [
            entry
            for entry in server.state.requests
            if entry[0] == "GET" and entry[1] == "file/sort"
        ]
        expect("run6 used folder fid", sort_requests[-1][2].get("pdir_fid"), "picked-1")

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
