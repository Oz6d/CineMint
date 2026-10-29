"""RSS → 115 订阅端到端测试（假 RSS、假 115、临时媒体目录）。

运行：cd backend && .venv/bin/python ../tests/test_rss_subscription.py
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["CINEMINT_DATA_DIR"] = tempfile.mkdtemp(prefix="cinemint-rss-sub-")
os.environ["CINEMINT_DEMO"] = "false"

import app.core.pipeline as pipeline  # noqa: E402
import app.core.rss as rss_core  # noqa: E402
import app.core.rss_subscription as rss_sub  # noqa: E402
import app.core.subscriptions as subscriptions  # noqa: E402
from app.core.metadata.tmdb import TMDBClient  # noqa: E402
from app.core.naming import DEFAULT_NAMING  # noqa: E402
from app.core.rss import FeedItem, FeedResult, RssError  # noqa: E402
from app.core.subscription_runs import create_run, finish_run, start_run  # noqa: E402
from app.db import dumps, execute, init_db, now, query_all, query_one, set_setting  # noqa: E402

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


def fake_match(parsed, folder, client, *, auto_threshold=0.72, review_threshold=0.5, folder_path=""):
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


def _bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(
            _bencode(key.encode() if isinstance(key, str) else key)
            + _bencode(value[key])
            for key in sorted(value)
        ) + b"e"
    raise TypeError(type(value))


TORRENT_INFO = {
    "name": b"Test.Show.S01E08.1080p.mkv",
    "length": 104857600,
    "piece length": 262144,
    "pieces": b"0" * 20,
}
TORRENT_BYTES = _bencode(
    {"announce": b"http://tracker.example/announce", "info": TORRENT_INFO}
)
TORRENT_HASH = hashlib.sha1(_bencode(TORRENT_INFO)).hexdigest()


class FakeP115:
    def __init__(self, staging: Path) -> None:
        self.staging = staging
        self.folder_parts: list[str] = []
        self.files: dict[str, tuple[str, bytes]] = {}
        self.by_hash: dict[str, tuple[str, bytes]] = {}
        self.failures: set[str] = set()
        self.tasks: dict[str, dict] = {}
        self.added: list[str] = []
        self.folders_used: list[str] = []
        self.counter = 0
        self.remap_tasks = False

    def close(self) -> None:
        return

    def ensure_folder_path(self, parts: list[str]) -> str:
        self.folder_parts = list(parts)
        return "folder-115"

    def list_subfolders(self, parts: list[str]) -> list[dict]:
        return []

    def list_entries(self, folder_id: str = "0") -> list[dict]:
        return []

    @staticmethod
    def _hash_of(url: str) -> str:
        match = re.search(r"btih:([0-9a-z]+)", url or "", re.I)
        return match.group(1).lower() if match else ""

    def _lookup(self, url: str) -> tuple[str, bytes] | None:
        entry = self.files.get(url)
        if entry:
            return entry
        return self.by_hash.get(self._hash_of(url))

    def add_task(self, url: str, *, folder_id: str) -> dict:
        self.folders_used.append(folder_id)
        self.counter += 1
        task_id = f"task-{self.counter}"
        entry = self._lookup(url)
        if entry is None:
            raise AssertionError(f"115 收到了无法映射的下载地址: {url}")
        task = {
            "id": task_id,
            "url": url,
            "info_hash": f"hash-{self.counter}",
            "name": entry[0],
            "status": "waiting",
            "percent": 0,
            "_file": entry,
        }
        self.tasks[task_id] = task
        self.added.append(url)
        return dict(task)

    def list_tasks(self) -> list[dict]:
        for task in self.tasks.values():
            if task["status"] in {"done", "failed"}:
                continue
            key = self._hash_of(task["url"]) or str(task.get("info_hash") or "")
            if task["url"] in self.failures or key in self.failures:
                task["status"] = "failed"
                task["error"] = "模拟 115 离线失败"
                continue
            name, content = task["_file"]
            target = self.staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            task["status"] = "done"
            task["percent"] = 100
        result = []
        for task in self.tasks.values():
            copy = dict(task)
            copy.pop("_file", None)
            if self.remap_tasks:
                copy["id"] = "normalized-" + str(copy["id"])
                copy["url"] = "https://cdn.115.com/" + str(copy["info_hash"])
            result.append(copy)
        return result


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

    media = Path(tempfile.mkdtemp(prefix="cinemint-rss-media-"))
    inbox = media / "115" / "待整理"
    target = media / "115" / "For infuse"
    staging = inbox / "_订阅" / "RSS"
    staging.mkdir(parents=True)
    target.mkdir(parents=True)

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
            "115 测试库",
            "115/待整理",
            "115/For infuse",
            "tv",
            dumps(naming),
            dumps({"enabled": False}),
            now(),
            now(),
        ),
    )
    subscription_id = execute(
        "INSERT INTO subscriptions(name,type,enabled,library_id,config,schedule,"
        "auto_organize,replace_versions,notify_channel_ids,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "RSS",
            "rss",
            1,
            library_id,
            dumps(
                {
                    "feed_urls": ["https://feed.example/rss"],
                    "save_subdir": "_订阅/RSS",
                    "max_per_run": 20,
                    "task_timeout": 60,
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

    fake_115 = FakeP115(staging)
    feed_state: dict[str, list[FeedItem]] = {"items": []}
    sent: list[dict] = []

    def fake_fetch(url: str) -> FeedResult:
        return FeedResult(url=url, title="RSS 测试源", items=list(feed_state["items"]))

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

    rss_sub.fetch_feed = fake_fetch
    rss_sub.p115_client = lambda: fake_115

    def fake_page_resolver(url: str, timeout: float = 20.0) -> tuple[str, str]:
        guid = url.rstrip("/").rsplit("/", 1)[-1]
        return f"magnet:?xt=urn:btih:{guid}&dn=Page", "magnet"

    rss_sub.resolve_page_download = fake_page_resolver
    rss_core.resolve_page_download = fake_page_resolver
    subscriptions.send_event = fake_send
    pipeline.match_item = fake_match
    TMDBClient.details = fake_details
    TMDBClient.season = fake_season

    def item(guid: str, title: str, url: str, kind: str = "torrent") -> FeedItem:
        return FeedItem(
            feed_url="https://feed.example/rss",
            guid=guid,
            title=title,
            link=f"https://feed.example/post/{guid}",
            download_url=url,
            download_kind=kind,
            size=100 * 1024 * 1024,
        )

    def download(url: str, name: str, content: bytes = b"video") -> None:
        fake_115.files[url] = (name, content)
        stem = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".torrent")
        fake_115.by_hash[stem.lower()] = (name, content)

    def run_once() -> dict:
        run_id = create_run(subscription_id, trigger="manual")
        start_run(run_id)
        row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
        result = rss_sub.run_rss_subscription(row, run_id)
        finish_run(
            run_id,
            result["status"],
            stats=result["stats"],
            message=result.get("message") or "",
        )
        return result

    # ---------- 第一次：两条新条目 ----------
    e01 = "[DKB] Test Show - S01E01 [1080p].mkv"
    e02 = "[DKB] Test Show - S01E02 [1080p].mkv"
    download("https://feed.example/1.torrent", e01, b"e01")
    download("https://feed.example/2.torrent", e02, b"e02")
    feed_state["items"] = [
        item("1", e01, "https://feed.example/1.torrent"),
        item("2", e02, "https://feed.example/2.torrent"),
    ]
    result = run_once()
    expect("run1 status", result["status"], "success")
    expect("run1 added", result["stats"].get("added"), 2)
    expect("run1 downloaded", result["stats"].get("downloaded"), 2)
    expect("run1 organized", result["stats"].get("organized"), 2)
    expect("run1 folder parts", fake_115.folder_parts, ["待整理", "_订阅", "RSS"])
    expect(
        "run1 entries organized",
        len(
            query_all(
                "SELECT * FROM subscription_entries WHERE subscription_id=? "
                "AND status='organized'",
                (subscription_id,),
            )
        ),
        2,
    )
    expect(
        "run1 target files",
        sorted(path.name for path in target.rglob("*.mkv")),
        ["Test Show S01E01.mkv", "Test Show S01E02.mkv"],
    )
    expect("run1 single aggregated push", len(sent), 1)
    expect("run1 transfer section", "【转存完成 · 2】" in summary_text(), True)
    expect("run1 organize section", "【整理完成 · 2】" in summary_text(), True)
    expect(
        "run1 match events",
        set(sent[0]["match_events"]) >= {"transfer.success", "organize.success"},
        True,
    )
    expect(
        "run1 no torrent urls",
        [url for url in fake_115.added if ".torrent" in url.lower()],
        [],
    )
    expect(
        "run1 magnet tasks",
        all(url.startswith("magnet:") for url in fake_115.added),
        True,
    )

    # ---------- 第二次：没有新条目 ----------
    sent.clear()
    result = run_once()
    expect("run2 status", result["status"], "success")
    expect("run2 added", result["stats"].get("added"), 0)
    expect("run2 skipped", result["stats"].get("skipped"), 2)
    expect("run2 target count", len(list(target.rglob("*.mkv"))), 2)

    # ---------- 第三次：普通重复 ----------
    sent.clear()
    e03 = "[DKB] Test Show - S01E03 [1080p].mkv"
    e03_dup = "[DKB] Test Show - S01E03 [720p].mkv"
    download("https://feed.example/3.torrent", e03)
    feed_state["items"].append(item("3", e03, "https://feed.example/3.torrent"))
    result = run_once()
    expect("run3 organized", result["stats"].get("organized"), 1)
    expect("run3 target count", len(list(target.rglob("*.mkv"))), 3)

    sent.clear()
    download("https://feed.example/99.torrent", e03_dup)
    feed_state["items"].append(item("99", e03_dup, "https://feed.example/99.torrent"))
    result = run_once()
    expect("run4 duplicate", result["stats"].get("duplicate"), 1)
    expect("run4 duplicate notify", "【重复 · 1】" in summary_text(), True)
    expect("run4 single aggregated push", len(sent), 1)
    expect("run4 target count", len(list(target.rglob("*.mkv"))), 3)
    duplicate_entry = query_one(
        "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_path=?",
        (subscription_id, "https://feed.example/post/99"),
    )
    expect("run4 entry status", duplicate_entry["status"], "duplicate")

    # ---------- 第四次：V2 替换 ----------
    sent.clear()
    e02_v2 = "[DKB] Test Show - S01E02 [1080p][V2].mkv"
    download("https://feed.example/102.torrent", e02_v2, b"e02v2")
    feed_state["items"].append(item("102", e02_v2, "https://feed.example/102.torrent"))
    result = run_once()
    expect("run5 replaced", result["stats"].get("replaced"), 1)
    expect("run5 organized", result["stats"].get("organized"), 1)
    expect("run5 replaced notify", "【V2 替换 · 1】" in summary_text(), True)
    expect(
        "run5 one e02",
        sorted(path.name for path in target.rglob("*S01E02*.mkv")),
        ["Test Show S01E02.mkv"],
    )
    v2_target = next(target.rglob("*S01E02*.mkv"))
    expect("run5 e02 content", v2_target.read_bytes(), b"e02v2")

    # ---------- 第五次：详情页 magnet ----------
    sent.clear()
    e04 = "[DKB] Test Show - S01E04 [1080p].mkv"
    download("https://feed.example/4.torrent", e04)
    feed_state["items"].append(
        item("4", e04, "https://feed.example/page/4", kind="page")
    )
    result = run_once()
    expect("run6 page organized", result["stats"].get("organized"), 1)
    expect("run6 target count", len(list(target.rglob("*.mkv"))), 4)
    expect("run6 page magnet used", fake_115.added[-1], "magnet:?xt=urn:btih:4&dn=Page")

    # ---------- 第六次：无法识别 ----------
    sent.clear()
    mystery = "[Group] Mystery Show S01E01.mkv"
    download("https://feed.example/mystery.torrent", mystery)
    feed_state["items"].append(
        item("mystery", mystery, "https://feed.example/mystery.torrent")
    )
    result = run_once()
    expect("run7 unmatched", result["stats"].get("unmatched"), 1)
    expect("run7 unmatched notify", "【无法识别 · 1】" in summary_text(), True)
    expect("run7 unmatched file listed", mystery in summary_text(), True)
    mystery_entry = query_one(
        "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_path=?",
        (subscription_id, "https://feed.example/post/mystery"),
    )
    expect("run7 entry status", mystery_entry["status"], "unmatched")

    # ---------- 第七次：115 任务失败 ----------
    sent.clear()
    fail_item = "[DKB] Test Show - S01E05 [1080p].mkv"
    fail_url = "https://feed.example/broken.torrent"
    download(fail_url, fail_item, b"broken")
    fake_115.failures.add("broken")
    feed_state["items"].append(item("broken", fail_item, fail_url))
    result = run_once()
    expect("run8 failed", result["stats"].get("failed"), 1)
    expect("run8 transfer failed notify", "【转存失败" in summary_text(), True)
    failed_entry = query_one(
        "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_path=?",
        (subscription_id, "https://feed.example/post/broken"),
    )
    expect("run8 entry status", failed_entry["status"], "failed")
    expect("run8 no target", len(list(target.rglob("*S01E05*.mkv"))), 0)

    # ---------- 第八次：115 规范化任务 ID / URL，按 info_hash 关联 ----------
    sent.clear()
    fake_115.remap_tasks = True
    e06 = "[DKB] Test Show - S01E06 [1080p].mkv"
    download("https://feed.example/6.torrent", e06)
    feed_state["items"].append(item("6", e06, "https://feed.example/6.torrent"))
    result = run_once()
    expect("run9 remapped organized", result["stats"].get("organized"), 1)
    expect("run9 target count", len(list(target.rglob("*.mkv"))), 5)
    remapped_entry = query_one(
        "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_path=?",
        (subscription_id, "https://feed.example/post/6"),
    )
    expect(
        "run9 task id remapped",
        str(remapped_entry["task_id"]).startswith("normalized-"),
        True,
    )

    # ---------- 第十次：详情页没有磁力时，种子自动转磁力 ----------
    sent.clear()
    original_fetch_torrent = rss_core.fetch_torrent

    def no_magnet_page(url: str, timeout: float = 20.0) -> tuple[str, str]:
        raise RssError("详情页没有磁力")

    rss_core.resolve_page_download = no_magnet_page
    rss_core.fetch_torrent = lambda url, timeout=20.0: TORRENT_BYTES
    try:
        e08 = "[DKB] Test Show - S01E08 [1080p].mkv"
        fake_115.by_hash[TORRENT_HASH] = (e08, b"e08")
        feed_state["items"].append(item("8", e08, "https://feed.example/8.torrent"))
        result = run_once()
    finally:
        rss_core.resolve_page_download = fake_page_resolver
        rss_core.fetch_torrent = original_fetch_torrent
    expect("run10 fallback organized", result["stats"].get("organized"), 1)
    expect("run10 target count", len(list(target.rglob("*.mkv"))), 6)
    expect(
        "run10 fallback magnet",
        fake_115.added[-1],
        f"magnet:?xt=urn:btih:{TORRENT_HASH}",
    )

    # ---------- 第十一次：RSS 全部失败 ----------
    sent.clear()
    rss_sub.fetch_feed = lambda url: FeedResult(
        url=url, title="", items=[], error="HTTP 500"
    )
    result = run_once()
    expect("run10 feed failure status", result["status"], "failed")
    expect("run10 run failed notify", any(e["event"] == "run.failed" for e in sent), True)

    # ---------- 第十二次：使用选定的 115 目录 ID ----------
    sent.clear()
    rss_sub.fetch_feed = fake_fetch
    execute(
        "UPDATE subscriptions SET config=? WHERE id=?",
        (
            dumps(
                {
                    "feed_urls": ["https://feed.example/rss"],
                    "save_subdir": "_订阅/RSS",
                    "max_per_run": 20,
                    "task_timeout": 60,
                    "p115_folder_id": "custom-115",
                }
            ),
            subscription_id,
        ),
    )
    parts_before = list(fake_115.folder_parts)
    e07 = "[DKB] Test Show - S01E07 [1080p].mkv"
    download("https://feed.example/7.torrent", e07)
    feed_state["items"].append(item("7", e07, "https://feed.example/7.torrent"))
    result = run_once()
    expect("run11 organized", result["stats"].get("organized"), 1)
    expect("run11 folder id used", fake_115.folders_used[-1], "custom-115")
    expect("run11 path not re-resolved", fake_115.folder_parts, parts_before)

    # ---------- 同一批就存下 v1 + v2：保留 v2，v1 记为重复 ----------
    sent.clear()
    e09_v1 = "[DKB] Test Show - S09E01 [1080p].mkv"
    e09_v2 = "[DKB] Test Show - S09E01 [1080p][V2].mkv"
    download("https://feed.example/901.torrent", e09_v1, b"e09v1")
    download("https://feed.example/902.torrent", e09_v2, b"e09v2")
    feed_state["items"].append(item("901", e09_v1, "https://feed.example/901.torrent"))
    feed_state["items"].append(item("902", e09_v2, "https://feed.example/902.torrent"))
    result = run_once()
    expect("run12 same-batch v2 organized", result["stats"].get("organized"), 1)
    expect("run12 same-batch v1 duplicate", result["stats"].get("duplicate"), 1)
    expect("run12 same-batch no replace", result["stats"].get("replaced"), 0)
    e09_targets = list(target.rglob("*S09E01*.mkv"))
    expect("run12 same-batch one file", len(e09_targets), 1)
    expect("run12 same-batch v2 content", e09_targets[0].read_bytes() if e09_targets else b"", b"e09v2")
    e09_items = {
        int(row["version"]): row
        for row in query_all(
            "SELECT * FROM items WHERE library_id=? AND season=9 AND episode=1",
            (library_id,),
        )
    }
    expect("run12 same-batch v2 item organized", e09_items.get(2, {}).get("status"), "organized")
    expect("run12 same-batch v1 item ignored", e09_items.get(0, {}).get("status"), "ignored")
    e09_v1_entry = query_one(
        "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_path=?",
        (subscription_id, "https://feed.example/post/901"),
    )
    e09_v2_entry = query_one(
        "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_path=?",
        (subscription_id, "https://feed.example/post/902"),
    )
    expect("run12 same-batch v1 entry", e09_v1_entry["status"], "duplicate")
    expect("run12 same-batch v2 entry", e09_v2_entry["status"], "organized")

    # 再跑一次：被判重复的 v1 不能被重扫复活成可整理状态
    result = run_once()
    expect(
        "run12b duplicate stays ignored",
        query_one("SELECT status FROM items WHERE id=?", (e09_items[0]["id"],))["status"],
        "ignored",
    )
    expect("run12b target still v2", e09_targets[0].read_bytes(), b"e09v2")
    expect("run12b no duplicate reorganized", result["stats"].get("organized"), 0)

    # ---------- 已整理 v2 后再来 v1：不上报替换、不降级覆盖 ----------
    sent.clear()
    e09e02_v2 = "[DKB] Test Show - S09E02 [1080p][V2].mkv"
    download("https://feed.example/903.torrent", e09e02_v2, b"e09e02v2")
    feed_state["items"].append(item("903", e09e02_v2, "https://feed.example/903.torrent"))
    result = run_once()
    expect("run13 v2 organized", result["stats"].get("organized"), 1)
    sent.clear()
    e09e02_v1 = "[DKB] Test Show - S09E02 [1080p].mkv"
    download("https://feed.example/904.torrent", e09e02_v1, b"e09e02v1")
    feed_state["items"].append(item("904", e09e02_v1, "https://feed.example/904.torrent"))
    result = run_once()
    expect("run14 downgrade duplicate", result["stats"].get("duplicate"), 1)
    expect("run14 downgrade no replace", result["stats"].get("replaced"), 0)
    e09e02_target = next(target.rglob("*S09E02*.mkv"))
    expect("run14 downgrade keeps v2", e09e02_target.read_bytes(), b"e09e02v2")

    # ---------- 手动匹配后同集 v1 + v2：仍按版本取 v2 ----------
    manual_base = "[Sakurato] Manual Test Show [01]"
    manual_v2 = "[Sakurato] Manual Test Show [01v2]"
    manual_match = dumps(
        {
            "tmdb_id": 123,
            "media_type": "tv",
            "title": "Test Show",
            "year": 2024,
        }
    )
    manual_ids: dict[int, int] = {}
    for version, name in ((0, manual_base), (2, manual_v2)):
        source = staging / f"{name}.mkv"
        source.write_bytes(f"manual-{version}".encode())
        parsed = dumps(
            {
                "title": "Manual Test Show",
                "season": 8,
                "episode": 8,
                "version": version,
                "media_type": "tv",
            }
        )
        manual_ids[version] = execute(
            "INSERT INTO items(library_id,source_path,file_name,size,mtime,parsed,match,"
            "match_key,status,season,episode,version,manual,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,'matched',8,8,?,1,?,?)",
            (
                library_id,
                str(source),
                source.name,
                source.stat().st_size,
                source.stat().st_mtime,
                parsed,
                manual_match,
                "tv:123",
                version,
                now(),
                now(),
            ),
        )
    manual_entries = [
        {
            "id": 9101,
            "item_id": manual_ids[0],
            "status": "unmatched",
            "season": None,
            "episode": 1,
            "file_name": manual_base,
        },
        {
            "id": 9102,
            "item_id": manual_ids[2],
            "status": "unmatched",
            "season": None,
            "episode": 1,
            "file_name": manual_v2,
        },
    ]
    manual_items = query_all(
        "SELECT * FROM items WHERE id IN (?,?) ORDER BY version",
        (manual_ids[0], manual_ids[2]),
    )
    manual_assigned = rss_sub._attribute_items(
        manual_entries, manual_items, str(staging), set(manual_ids.values())
    )
    expect(
        "manual linked attribution",
        [item["id"] for item in manual_assigned.get(9101, [])],
        [manual_ids[0]],
    )
    expect(
        "manual v2 linked attribution",
        [item["id"] for item in manual_assigned.get(9102, [])],
        [manual_ids[2]],
    )
    class _SilentHooks:
        def log(self, *args, **kwargs) -> None:
            return

    manual_stats = defaultdict(int)
    manual_plans = subscriptions._plan_duplicates(
        query_one("SELECT * FROM libraries WHERE id=?", (library_id,)),
        query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,)),
        [(item, None) for item in manual_items],
        _SilentHooks(),
        manual_stats,
    )
    expect(
        "manual v1+v2 keeps only v2",
        [(plan["item"]["version"], [old["version"] for old in plan["replace"]]) for plan in manual_plans],
        [(2, [])],
    )
    expect("manual v1 duplicate counted", manual_stats["duplicate"], 1)

    # ---------- 条目 → 文件关联：重跑时任务名缺失也能正确归属 ----------
    linked_item = {
        "id": 424242,
        "source_path": str(staging / "不同目录" / "不同文件名.mkv"),
        "file_name": "不同文件名.mkv",
        "season": 1,
        "episode": 4,
        "status": "matched",
    }
    linked_entries = [
        {
            "id": 9001,
            "item_id": 424242,
            "status": "matched",
            "season": None,
            "episode": None,
            "file_name": "完全不同的 RSS 标题 [04]",
        }
    ]
    assigned = rss_sub._attribute_items(
        linked_entries, [linked_item], str(staging), {424242}
    )
    expect("entry item_id attribution", assigned.get(9001), [linked_item])

    # ---------- 重新读取 RSS 不覆盖上一轮的 115 任务信息 ----------
    first_feed = feed_state["items"][0]
    first_entry = query_one(
        "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_key=?",
        (subscription_id, first_feed.remote_key),
    )
    raw = rss_sub._entry_raw(first_entry)
    raw["task_name"] = "上一轮任务名"
    raw["task_url"] = "magnet:?xt=urn:btih:keep"
    execute(
        "UPDATE subscription_entries SET raw=? WHERE id=?",
        (dumps(raw), first_entry["id"]),
    )
    rss_sub._upsert_entry(subscription_id, first_feed)
    kept = rss_sub._entry_raw(
        query_one("SELECT * FROM subscription_entries WHERE id=?", (first_entry["id"],))
    )
    expect("entry task name kept", kept.get("task_name"), "上一轮任务名")
    expect("entry task url kept", kept.get("task_url"), "magnet:?xt=urn:btih:keep")

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
