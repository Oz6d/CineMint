"""订阅 API 回归测试：CRUD、密钥保护、计划校验、夸克目录与预览。

运行：cd backend && .venv/bin/python ../tests/test_subscriptions_api.py
使用临时数据库、临时媒体目录和本地假夸克接口。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEMP_DATA = tempfile.mkdtemp(prefix="cinemint-subscriptions-api-")
os.environ["CINEMINT_DATA_DIR"] = TEMP_DATA
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from fastapi.testclient import TestClient  # noqa: E402

from quark_mock import MockQuarkServer  # noqa: E402

import app.api.subscriptions as subscriptions_api  # noqa: E402
from app.core.naming import DEFAULT_NAMING  # noqa: E402
from app.core.quark import QuarkClient  # noqa: E402
from app.core.rss import FeedItem, FeedResult  # noqa: E402
from app.core.subscriptions import decode_config  # noqa: E402
from app.db import dumps, execute, loads, now, query_one  # noqa: E402
from app.main import app  # noqa: E402


SHARE_URL = "https://pan.quark.cn/s/abc123def456"
PASSCODE = "1234"


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

    media = Path(tempfile.mkdtemp(prefix="cinemint-subscriptions-api-media-"))
    for relative in ("待整理", "For infuse", "待整理2", "For infuse2"):
        (media / relative).mkdir(parents=True, exist_ok=True)

    with MockQuarkServer() as server, TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cinemint"})
        if login.status_code != 200:
            print(f"FAIL login: {login.status_code} {login.text}")
            return 1

        server.state.add_folder("Movies", my_drive=True)
        season_fid = server.state.add_folder("Season 1")
        server.state.add_file(
            "[DKB] Dragon Ball Super Beerus - S01E01 [1080p][V2].mkv",
            parent=season_fid,
            fid="f1",
        )
        server.state.add_file(
            "[DKB] Dragon Ball Super Beerus - S01E02 [1080p][HEVC x265 10bit].mkv",
            parent=season_fid,
            fid="f2",
        )
        server.state.add_file("readme.txt", parent=season_fid, fid="f3")
        server.state.save_root = media / "待整理" / "_订阅" / "API"
        server.state.require_passcode = PASSCODE
        server.state.passcode = PASSCODE

        subscriptions_api.quark_client = lambda: QuarkClient(
            "ck", base_url=server.url, qps=0
        )

        source_id = execute(
            "INSERT INTO sources(name,type,config,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("本地测试", "local", dumps({"base_path": str(media)}), now(), now()),
        )
        naming = {**DEFAULT_NAMING, "auto_category": False, "episode_title": False}

        def add_library(name: str, root: str, target: str) -> int:
            return execute(
                "INSERT INTO libraries(source_id,name,root_path,target_root,media_type,"
                "naming,scrape,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    name,
                    root,
                    target,
                    "tv",
                    dumps(naming),
                    dumps({"enabled": False}),
                    now(),
                    now(),
                ),
            )

        library_id = add_library("测试库", "待整理", "For infuse")
        other_library_id = add_library("备用库", "待整理2", "For infuse2")
        p115_library_id = add_library("115 测试库", "115/待整理", "115/For infuse")
        tree_library_id = add_library(
            "夸克树库", "夸克网盘/待整理", "夸克网盘/For infuse"
        )

        # ---------- 预览 ----------
        preview = client.post(
            "/api/subscriptions/preview",
            json={
                "name": "API",
                "config": {
                    "share_url": SHARE_URL,
                    "passcode": PASSCODE,
                    "keyword": "Dragon Ball Super Beerus",
                },
            },
        )
        data = preview.json()
        expect("preview status", preview.status_code, 200)
        expect("preview total", data.get("total"), 3)
        expect("preview matched", data.get("matched"), 2)
        expect(
            "preview versions",
            [item["version"] for item in data.get("samples", [])],
            [2, 0],
        )
        expect("preview page default", data.get("page"), 1)
        expect("preview total pages", data.get("total_pages"), 1)
        expect("preview has more", data.get("has_more"), False)

        paged = client.post(
            "/api/subscriptions/preview",
            json={
                "name": "API",
                "config": {
                    "share_url": SHARE_URL,
                    "passcode": PASSCODE,
                    "keyword": "Dragon Ball Super Beerus",
                },
                "page": 2,
                "page_size": 1,
            },
        )
        paged_data = paged.json()
        expect("preview paged status", paged.status_code, 200)
        expect("preview paged page", paged_data.get("page"), 2)
        expect("preview paged total pages", paged_data.get("total_pages"), 2)
        expect("preview paged has more", paged_data.get("has_more"), False)
        expect(
            "preview paged version",
            [item["version"] for item in paged_data.get("samples", [])],
            [0],
        )
        over_page = client.post(
            "/api/subscriptions/preview",
            json={
                "name": "API",
                "config": {
                    "share_url": SHARE_URL,
                    "passcode": PASSCODE,
                    "keyword": "Dragon Ball Super Beerus",
                },
                "page": 99,
                "page_size": 1,
            },
        )
        expect("preview page clamps", over_page.json().get("page"), 2)

        wrong_passcode = client.post(
            "/api/subscriptions/preview",
            json={
                "name": "API",
                "config": {
                    "share_url": SHARE_URL,
                    "passcode": "9999",
                    "keyword": "Dragon Ball",
                },
            },
        )
        expect("preview wrong passcode", wrong_passcode.status_code, 400)

        bad_regex = client.post(
            "/api/subscriptions/preview",
            json={
                "name": "API",
                "config": {"share_url": SHARE_URL, "regex": "([bad"},
            },
        )
        expect("preview bad regex", bad_regex.status_code, 400)

        # ---------- 夸克目录 ----------
        folders = client.get(
            "/api/subscriptions/quark/folders", params={"library_id": library_id}
        )
        expect("folder root status", folders.status_code, 200)
        expect(
            "folder root entries",
            [item["name"] for item in folders.json().get("entries", [])],
            ["Movies"],
        )

        mkdir = client.post(
            "/api/subscriptions/quark/folders",
            json={"library_id": library_id, "path": "_订阅/API"},
        )
        expect("mkdir status", mkdir.status_code, 200)
        listed = client.get(
            "/api/subscriptions/quark/folders",
            params={"library_id": library_id, "path": "_订阅"},
        )
        expect(
            "mkdir visible",
            [item["name"] for item in listed.json().get("entries", [])],
            ["API"],
        )

        unsafe = client.get(
            "/api/subscriptions/quark/folders",
            params={"library_id": library_id, "path": "../etc"},
        )
        expect("folder traversal blocked", unsafe.status_code, 400)

        missing_library = client.get(
            "/api/subscriptions/quark/folders", params={"library_id": 9999}
        )
        expect("folder missing library", missing_library.status_code, 404)

        # ---------- 创建 ----------
        body = {
            "name": "夸克测试",
            "library_id": library_id,
            "type": "quark",
            "enabled": True,
            "config": {
                "share_url": SHARE_URL,
                "passcode": PASSCODE,
                "keyword": "Dragon Ball",
                "save_subdir": "_订阅/API",
                "max_per_run": 5,
                "visibility_timeout": 30,
            },
            "schedule": {"mode": "interval", "interval_minutes": 30},
            "deadline": "2026-12-31",
            "auto_organize": True,
            "replace_versions": True,
            "notify_channel_ids": [3, 4],
        }
        created = client.post("/api/subscriptions", json=body)
        created_data = created.json()
        expect("create status", created.status_code, 200)
        subscription_id = created_data.get("id")
        expect("create masked passcode", created_data["config"]["passcode"], "")
        expect("create passcode flag", created_data["config"]["passcode_set"], True)
        expect("create library name", created_data["library_name"], "测试库")
        expect("create entries empty", created_data["entry_counts"], {})
        expect("create next run", bool(created_data["next_run_at"]), True)
        expect(
            "create immediately queued", created_data["next_run_at"] <= now(), True
        )
        expect("create notify ids", created_data["notify_channel_ids"], [3, 4])

        stored = loads(
            query_one("SELECT config FROM subscriptions WHERE id=?", (subscription_id,))[
                "config"
            ],
            {},
        )
        expect("stored passcode encrypted", stored["passcode"].startswith("enc:"), True)
        expect(
            "stored passcode decrypts", decode_config(stored)["passcode"], PASSCODE
        )

        duplicate_name = client.post("/api/subscriptions", json=body)
        expect("duplicate name allowed", duplicate_name.status_code, 200)
        duplicate_id = duplicate_name.json()["id"]

        invalid_type = client.post(
            "/api/subscriptions", json={**body, "type": "ftp"}
        )
        expect("invalid type", invalid_type.status_code, 400)

        invalid_cron = client.post(
            "/api/subscriptions",
            json={**body, "schedule": {"mode": "cron", "cron": "bad cron"}},
        )
        expect("invalid cron create", invalid_cron.status_code, 400)

        invalid_deadline = client.post(
            "/api/subscriptions", json={**body, "deadline": "not-a-date"}
        )
        expect("invalid deadline create", invalid_deadline.status_code, 400)

        listing = client.get("/api/subscriptions")
        expect("list status", listing.status_code, 200)
        expect("list size", len(listing.json()), 2)
        expect(
            "list hides passcode",
            all(item["config"]["passcode"] == "" for item in listing.json()),
            True,
        )

        # ---------- 更新 ----------
        update_body = {
            **body,
            "name": "夸克测试改名",
            "config": {
                "share_url": SHARE_URL,
                "passcode": "",
                "keyword": "Dragon Ball Super Beerus",
                "save_subdir": "_订阅/API",
                "max_per_run": 8,
                "visibility_timeout": 30,
            },
            "schedule": {"mode": "cron", "cron": "0 9 * * *"},
            "auto_organize": False,
        }
        updated = client.put(f"/api/subscriptions/{subscription_id}", json=update_body)
        updated_data = updated.json()
        expect("update status", updated.status_code, 200)
        expect("update name", updated_data["name"], "夸克测试改名")
        expect("update passcode flag", updated_data["config"]["passcode_set"], True)
        expect("update schedule mode", updated_data["schedule"]["mode"], "cron")
        expect("update auto organize", updated_data["auto_organize"], False)
        expect("update next run", bool(updated_data["next_run_at"]), True)
        stored = loads(
            query_one("SELECT config FROM subscriptions WHERE id=?", (subscription_id,))[
                "config"
            ],
            {},
        )
        expect(
            "update kept passcode", decode_config(stored)["passcode"], PASSCODE
        )

        invalid_cron = client.put(
            f"/api/subscriptions/{subscription_id}",
            json={**update_body, "schedule": {"mode": "cron", "cron": "61 9 * * *"}},
        )
        expect("invalid cron update", invalid_cron.status_code, 400)

        invalid_deadline = client.put(
            f"/api/subscriptions/{subscription_id}",
            json={**update_body, "deadline": "2026-99-99"},
        )
        expect("invalid deadline update", invalid_deadline.status_code, 400)

        missing_library = client.put(
            f"/api/subscriptions/{subscription_id}",
            json={**update_body, "library_id": 9999},
        )
        expect("update missing library", missing_library.status_code, 404)

        # ---------- 条目 ----------
        for remote_key, status in (("f1", "organized"), ("f2", "failed")):
            execute(
                "INSERT INTO subscription_entries(subscription_id, remote_key, file_name, "
                "status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (subscription_id, remote_key, f"{remote_key}.mkv", status, now(), now()),
            )
        entries = client.get(f"/api/subscriptions/{subscription_id}/entries")
        expect("entries status", entries.status_code, 200)
        expect("entries size", len(entries.json()), 2)
        filtered = client.get(
            f"/api/subscriptions/{subscription_id}/entries",
            params={"status": "organized"},
        )
        expect(
            "entries filter", [row["file_name"] for row in filtered.json()], ["f1.mkv"]
        )
        limited = client.get(
            f"/api/subscriptions/{subscription_id}/entries", params={"limit": 1}
        )
        expect("entries limit", len(limited.json()), 1)
        expect(
            "entries missing subscription",
            client.get("/api/subscriptions/9999/entries").status_code,
            404,
        )

        # 切换影视库会清空台账，避免旧路径被误用到新库。
        moved = client.put(
            f"/api/subscriptions/{subscription_id}",
            json={**update_body, "library_id": other_library_id},
        )
        expect("move library status", moved.status_code, 200)
        expect("move library name", moved.json()["library_name"], "备用库")
        expect(
            "move library cleared entries",
            len(client.get(f"/api/subscriptions/{subscription_id}/entries").json()),
            0,
        )

        # ---------- 导出任务参数（含提取码，供快速新建） ----------
        task_params = client.get(
            f"/api/subscriptions/{subscription_id}/task-params"
        )
        task_data = task_params.json()
        expect("task params status", task_params.status_code, 200)
        expect("task params name", task_data.get("name"), "夸克测试改名")
        expect("task params type", task_data.get("type"), "quark")
        expect("task params passcode", task_data["config"].get("passcode"), PASSCODE)
        expect("task params passcode set", task_data["config"].get("passcode_set"), True)
        expect("task params share url", task_data["config"].get("share_url"), SHARE_URL)
        expect("task params schedule", task_data.get("schedule", {}).get("mode"), "cron")
        expect("task params auto organize", task_data.get("auto_organize"), False)
        expect(
            "task params missing",
            client.get("/api/subscriptions/9999/task-params").status_code,
            404,
        )
        # 列表接口仍然不返回提取码，只有导出任务参数才返回。
        listed = next(
            item
            for item in client.get("/api/subscriptions").json()
            if item["id"] == subscription_id
        )
        expect("task params list still hides passcode", listed["config"].get("passcode"), "")

        # ---------- 夸克转存目录留空 = 影视库根目录 ----------
        blank_body = {
            "name": "夸克根目录留空",
            "library_id": library_id,
            "type": "quark",
            "config": {
                "share_url": SHARE_URL,
                "passcode": PASSCODE,
                "max_per_run": 5,
            },
            "schedule": {"mode": "interval", "interval_minutes": 30},
        }
        blank_created = client.post("/api/subscriptions", json=blank_body)
        blank_data = blank_created.json()
        expect("blank quark status", blank_created.status_code, 200)
        blank_id = blank_data.get("id")
        expect("blank quark subdir", blank_data["config"].get("save_subdir"), "")
        expect("blank quark root", blank_data["config"].get("save_root"), True)
        stored_blank = decode_config(
            loads(
                query_one(
                    "SELECT config FROM subscriptions WHERE id=?", (blank_id,)
                )["config"],
                {},
            )
        )
        expect("blank quark stored root", stored_blank.get("save_root"), True)
        expect("blank quark stored subdir", stored_blank.get("save_subdir"), "")
        blank_updated = client.put(
            f"/api/subscriptions/{blank_id}",
            json={**blank_body, "name": "夸克根目录留空改"},
        )
        expect("blank quark update status", blank_updated.status_code, 200)
        expect(
            "blank quark update root",
            blank_updated.json()["config"].get("save_root"),
            True,
        )
        # 旧数据：存了空 save_subdir 但没有 save_root 标记时也按根目录展示。
        legacy_id = execute(
            "INSERT INTO subscriptions(name,type,enabled,library_id,config,schedule,"
            "auto_organize,replace_versions,notify_channel_ids,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "旧版留空订阅",
                "quark",
                0,
                library_id,
                dumps({"share_url": SHARE_URL, "save_subdir": ""}),
                dumps({"mode": "interval", "interval_minutes": 30}),
                0,
                1,
                "[]",
                now(),
                now(),
            ),
        )
        legacy = client.get(f"/api/subscriptions/{legacy_id}").json()
        expect("legacy blank root", legacy["config"].get("save_root"), True)
        expect("legacy blank subdir", legacy["config"].get("save_subdir"), "")

        # ---------- RSS 预览 ----------
        def rss_item(guid: str, title: str, url: str) -> FeedItem:
            return FeedItem(
                feed_url="https://mikanani.me/RSS/Test",
                guid=guid,
                title=title,
                link=f"https://mikanani.me/Episode/{guid}",
                download_url=url,
                download_kind="magnet",
                size=120 * 1024 * 1024,
            )

        feed_items = [
            rss_item("m1", "[DKB] Test Show - S01E01 [1080p].mkv", "magnet:?xt=urn:btih:m1"),
            rss_item("m2", "[DKB] Test Show - S01E02 [1080p].mkv", "magnet:?xt=urn:btih:m2"),
            rss_item("m3", "[Other] Another Show - S01E01.mkv", "magnet:?xt=urn:btih:m3"),
        ]
        subscriptions_api.fetch_feed = lambda url: FeedResult(
            url=url, title="蜜柑计划", items=list(feed_items)
        )
        rss_preview = client.post(
            "/api/subscriptions/preview",
            json={
                "name": "RSS 测试",
                "type": "rss",
                "config": {
                    "feed_urls": ["https://mikanani.me/RSS/Test"],
                    "keyword": "Test Show",
                },
            },
        )
        rss_data = rss_preview.json()
        expect("rss preview status", rss_preview.status_code, 200)
        expect("rss preview type", rss_data.get("type"), "rss")
        expect("rss preview total", rss_data.get("total"), 3)
        expect("rss preview matched", rss_data.get("matched"), 2)
        expect(
            "rss preview episodes",
            [item["episode"] for item in rss_data.get("samples", [])],
            [1, 2],
        )
        expect(
            "rss preview feed title",
            rss_data["feeds"][0]["title"],
            "蜜柑计划",
        )

        subscriptions_api.fetch_feed = lambda url: FeedResult(
            url=url, title="", items=[], error="HTTP 500"
        )
        failed_preview = client.post(
            "/api/subscriptions/preview",
            json={
                "type": "rss",
                "config": {"feed_urls": ["https://mikanani.me/RSS/Broken"]},
            },
        )
        expect("rss preview feed error status", failed_preview.status_code, 200)
        expect(
            "rss preview feed error",
            failed_preview.json()["feeds"][0]["error"],
            "HTTP 500",
        )
        missing_feed = client.post(
            "/api/subscriptions/preview",
            json={"type": "rss", "config": {}},
        )
        expect("rss preview missing feed", missing_feed.status_code, 400)
        bad_scheme = client.post(
            "/api/subscriptions/preview",
            json={"type": "rss", "config": {"feed_urls": ["ftp://example.com/rss"]}},
        )
        expect("rss preview bad scheme", bad_scheme.status_code, 400)
        bad_regex = client.post(
            "/api/subscriptions/preview",
            json={
                "type": "rss",
                "config": {
                    "feed_urls": ["https://mikanani.me/RSS/Test"],
                    "regex": "([bad",
                },
            },
        )
        expect("rss preview bad regex", bad_regex.status_code, 400)

        # ---------- RSS 创建与更新 ----------
        subscriptions_api.fetch_feed = lambda url: FeedResult(
            url=url, title="蜜柑计划", items=list(feed_items)
        )
        rss_body = {
            "name": "RSS 测试",
            "library_id": library_id,
            "type": "rss",
            "enabled": True,
            "config": {
                "feed_urls": "https://mikanani.me/RSS/Test",
                "keyword": "Test Show",
                "save_subdir": "_订阅/RSS",
                "max_per_run": 500,
                "task_timeout": 120,
            },
            "schedule": {"mode": "interval", "interval_minutes": 60},
            "auto_organize": True,
            "replace_versions": True,
            "notify_channel_ids": [],
        }
        rss_created = client.post("/api/subscriptions", json=rss_body)
        rss_created_data = rss_created.json()
        rss_id = rss_created_data.get("id")
        expect("rss create status", rss_created.status_code, 200)
        expect("rss create type", rss_created_data.get("type"), "rss")
        expect(
            "rss create feed urls",
            rss_created_data["config"]["feed_urls"],
            ["https://mikanani.me/RSS/Test"],
        )
        expect(
            "rss create save subdir",
            rss_created_data["config"]["save_subdir"],
            "_订阅/RSS",
        )
        expect(
            "rss create max clamp",
            rss_created_data["config"]["max_per_run"],
            200,
        )
        expect("rss create no passcode", "passcode" in rss_created_data["config"], False)
        expect("rss create next run", bool(rss_created_data["next_run_at"]), True)

        rss_update = client.put(
            f"/api/subscriptions/{rss_id}",
            json={
                **rss_body,
                "name": "RSS 测试改名",
                "config": {
                    "feed_urls": ["https://mikanani.me/RSS/Test"],
                    "save_subdir": "_订阅/RSS2",
                },
            },
        )
        expect("rss update status", rss_update.status_code, 200)
        expect(
            "rss update save subdir",
            rss_update.json()["config"]["save_subdir"],
            "_订阅/RSS2",
        )
        expect(
            "rss type immutable",
            client.put(
                f"/api/subscriptions/{rss_id}", json={**rss_body, "type": "quark"}
            ).status_code,
            400,
        )
        expect(
            "rss update missing feed",
            client.put(
                f"/api/subscriptions/{rss_id}",
                json={**rss_body, "config": {"save_subdir": "_订阅/RSS2"}},
            ).status_code,
            400,
        )
        expect(
            "rss update traversal",
            client.put(
                f"/api/subscriptions/{rss_id}",
                json={
                    **rss_body,
                    "config": {
                        "feed_urls": ["https://mikanani.me/RSS/Test"],
                        "save_subdir": "../etc",
                    },
                },
            ).status_code,
            400,
        )

        # ---------- 115 目录 ----------
        class FakeP115Folders:
            def __init__(self) -> None:
                self.listed: list[list[str]] = []
                self.created: list[list[str]] = []
                self.resolved: list[list[str]] = []
                self.listed_ids: list[str] = []
                self.created_ids: list[tuple[str, str]] = []

            def list_subfolders(self, parts):
                self.listed.append(list(parts))
                return [{"name": "API", "fid": "f-1"}]

            def ensure_folder_path(self, parts):
                self.created.append(list(parts))
                return "f-1"

            def resolve_folder(self, parts, create=False):
                self.resolved.append(list(parts))
                return "f-root" if parts else "0"

            def list_entries(self, folder_id="0"):
                self.listed_ids.append(folder_id)
                return [
                    {"name": "API", "fid": "f-1", "is_dir": True},
                    {"name": "file.mkv", "fid": "x-1", "is_dir": False},
                ]

            def create_folder(self, parent_id, name):
                self.created_ids.append((parent_id, name))
                return "f-new"

            def close(self) -> None:
                return

        fake_115 = FakeP115Folders()
        subscriptions_api.p115_client = lambda: fake_115
        p115_list = client.get(
            "/api/subscriptions/p115/folders",
            params={"library_id": p115_library_id, "path": "_订阅"},
        )
        expect("p115 folder status", p115_list.status_code, 200)
        expect("p115 folder path", p115_list.json()["p115_path"], "待整理/_订阅")
        expect("p115 folder parts", fake_115.listed[0], ["待整理", "_订阅"])
        expect(
            "p115 folder entries",
            [entry["path"] for entry in p115_list.json()["entries"]],
            ["_订阅/API"],
        )
        p115_root = client.get(
            "/api/subscriptions/p115/folders",
            params={
                "library_id": p115_library_id,
                "path": "_订阅",
                "p115_root": "115/待整理",
            },
        )
        expect(
            "p115 folder root override",
            p115_root.json()["p115_path"],
            "115/待整理/_订阅",
        )
        p115_mkdir = client.post(
            "/api/subscriptions/p115/folders",
            json={"library_id": p115_library_id, "path": "_订阅/RSS"},
        )
        expect("p115 mkdir status", p115_mkdir.status_code, 200)
        expect("p115 mkdir parts", fake_115.created[0], ["待整理", "_订阅", "RSS"])
        expect(
            "p115 folder traversal",
            client.get(
                "/api/subscriptions/p115/folders",
                params={"library_id": p115_library_id, "path": "../etc"},
            ).status_code,
            400,
        )
        expect(
            "p115 folder missing library",
            client.get(
                "/api/subscriptions/p115/folders", params={"library_id": 9999}
            ).status_code,
            404,
        )
        expect(
            "p115 mkdir empty path",
            client.post(
                "/api/subscriptions/p115/folders",
                json={"library_id": p115_library_id, "path": ""},
            ).status_code,
            400,
        )

        # ---------- 夸克目录树 ----------
        tree_base_fid = server.state.add_folder(
            "待整理", my_drive=True, fid="q-base"
        )
        tree_for_fid = server.state.add_folder(
            "For infuse", parent=tree_base_fid, my_drive=True, fid="q-for"
        )
        server.state.add_folder("其他", my_drive=True, fid="q-other")

        tree = client.get(
            "/api/subscriptions/quark/tree", params={"library_id": tree_library_id}
        )
        tree_data = tree.json()
        expect("quark tree status", tree.status_code, 200)
        expect("quark tree fid", tree_data.get("fid"), tree_base_fid)
        expect("quark tree path", tree_data.get("path"), "待整理")
        expect("quark tree library path", tree_data.get("library_path"), "")
        expect("quark tree inside", tree_data.get("inside_library"), True)
        expect(
            "quark tree entries",
            [(item["name"], item["path"]) for item in tree_data.get("entries", [])],
            [("For infuse", "待整理/For infuse")],
        )

        tree_for = client.get(
            "/api/subscriptions/quark/tree",
            params={
                "library_id": tree_library_id,
                "fid": tree_for_fid,
                "path": "待整理/For infuse",
            },
        )
        expect("quark tree child path", tree_for.json().get("library_path"), "For infuse")
        expect("quark tree child entries", tree_for.json().get("entries"), [])

        drive_tree = client.get(
            "/api/subscriptions/quark/tree",
            params={"library_id": tree_library_id, "from_root": "true"},
        )
        drive_data = drive_tree.json()
        expect("quark drive root path", drive_data.get("path"), "")
        expect("quark drive root outside", drive_data.get("inside_library"), False)
        expect(
            "quark drive root entries",
            [item["name"] for item in drive_data.get("entries", [])],
            ["_订阅", "Movies", "其他", "待整理"],
        )

        tree_mkdir = client.post(
            "/api/subscriptions/quark/tree",
            json={
                "library_id": tree_library_id,
                "fid": tree_base_fid,
                "path": "待整理",
                "name": "新番",
            },
        )
        expect("quark tree mkdir status", tree_mkdir.status_code, 200)
        expect("quark tree mkdir path", tree_mkdir.json().get("path"), "待整理/新番")
        expect(
            "quark tree mkdir invalid name",
            client.post(
                "/api/subscriptions/quark/tree",
                json={
                    "library_id": tree_library_id,
                    "fid": tree_base_fid,
                    "path": "待整理",
                    "name": "a/b",
                },
            ).status_code,
            400,
        )

        # OpenList 挂载名改名：影视库 root/target 同步为新挂载路径后，
        # 目录树仍能识别「是否在整理前目录」并映射出同样的库内相对路径。
        renamed_mount_library = add_library(
            "挂载改名库", "新挂载名/待整理", "新挂载名/For infuse"
        )
        renamed_tree = client.get(
            "/api/subscriptions/quark/tree",
            params={"library_id": renamed_mount_library},
        )
        renamed_data = renamed_tree.json()
        expect("renamed mount tree status", renamed_tree.status_code, 200)
        expect("renamed mount tree fid", renamed_data.get("fid"), tree_base_fid)
        expect("renamed mount tree path", renamed_data.get("path"), "待整理")
        expect("renamed mount tree inside", renamed_data.get("inside_library"), True)
        expect("renamed mount tree library path", renamed_data.get("library_path"), "")
        renamed_child = client.get(
            "/api/subscriptions/quark/tree",
            params={
                "library_id": renamed_mount_library,
                "fid": tree_for_fid,
                "path": "待整理/For infuse",
            },
        )
        expect(
            "renamed mount child inside",
            renamed_child.json().get("inside_library"),
            True,
        )
        expect(
            "renamed mount child library path",
            renamed_child.json().get("library_path"),
            "For infuse",
        )
        renamed_body = {
            "name": "挂载改名订阅",
            "library_id": renamed_mount_library,
            "type": "quark",
            "config": {
                "share_url": SHARE_URL,
                "passcode": PASSCODE,
                "quark_folder_id": tree_for_fid,
                "quark_folder_path": "待整理/For infuse",
                "max_per_run": 5,
                "visibility_timeout": 30,
            },
            "schedule": {"mode": "interval", "interval_minutes": 30},
        }
        renamed_sub = client.post("/api/subscriptions", json=renamed_body)
        expect("renamed mount subscription status", renamed_sub.status_code, 200)
        expect(
            "renamed mount mapped subdir",
            renamed_sub.json()["config"].get("save_subdir"),
            "For infuse",
        )
        expect(
            "renamed mount mapped root",
            renamed_sub.json()["config"].get("save_root"),
            False,
        )
        renamed_subscription_id = renamed_sub.json()["id"]

        folder_body = {
            "name": "夸克目录订阅",
            "library_id": tree_library_id,
            "type": "quark",
            "config": {
                "share_url": SHARE_URL,
                "passcode": PASSCODE,
                "quark_folder_id": tree_for_fid,
                "quark_folder_path": "待整理/For infuse",
                "max_per_run": 5,
                "visibility_timeout": 30,
            },
            "schedule": {"mode": "interval", "interval_minutes": 30},
        }
        folder_sub = client.post("/api/subscriptions", json=folder_body)
        folder_data = folder_sub.json()
        expect("quark folder create status", folder_sub.status_code, 200)
        folder_id = folder_data.get("id")
        expect(
            "quark folder mapped subdir", folder_data["config"].get("save_subdir"), "For infuse"
        )
        expect("quark folder mapped root", folder_data["config"].get("save_root"), False)
        expect(
            "quark folder id kept",
            folder_data["config"].get("quark_folder_id"),
            tree_for_fid,
        )

        root_selection = client.post(
            "/api/subscriptions",
            json={
                **folder_body,
                "name": "夸克根目录订阅",
                "config": {
                    **folder_body["config"],
                    "quark_folder_id": tree_base_fid,
                    "quark_folder_path": "待整理",
                },
            },
        )
        expect("quark root selection status", root_selection.status_code, 200)
        expect(
            "quark root selection subdir",
            root_selection.json()["config"].get("save_subdir"),
            "",
        )
        expect(
            "quark root selection root", root_selection.json()["config"].get("save_root"), True
        )

        outside_selection = client.post(
            "/api/subscriptions",
            json={
                **folder_body,
                "name": "夸克库外订阅",
                "config": {
                    **folder_body["config"],
                    "quark_folder_id": "q-other",
                    "quark_folder_path": "其他",
                },
            },
        )
        expect("quark outside selection blocked", outside_selection.status_code, 400)

        folder_updated = client.put(
            f"/api/subscriptions/{folder_id}",
            json={
                **folder_body,
                "config": {
                    **folder_body["config"],
                    "quark_folder_id": tree_base_fid,
                    "quark_folder_path": "待整理",
                },
            },
        )
        expect("quark folder update status", folder_updated.status_code, 200)
        expect(
            "quark folder update root", folder_updated.json()["config"].get("save_root"), True
        )

        # ---------- 115 目录树 ----------
        p115_tree = client.get(
            "/api/subscriptions/p115/tree", params={"library_id": p115_library_id}
        )
        p115_tree_data = p115_tree.json()
        expect("p115 tree status", p115_tree.status_code, 200)
        expect("p115 tree fid", p115_tree_data.get("fid"), "f-root")
        expect("p115 tree path", p115_tree_data.get("path"), "待整理")
        expect("p115 tree library path", p115_tree_data.get("library_path"), "")
        expect(
            "p115 tree entries",
            [
                (item["name"], item["path"])
                for item in p115_tree_data.get("entries", [])
            ],
            [("API", "待整理/API")],
        )
        expect(
            "p115 tree resolved parts", fake_115.resolved[-1], ["待整理"]
        )

        p115_drive = client.get(
            "/api/subscriptions/p115/tree",
            params={"library_id": p115_library_id, "from_root": "true"},
        )
        expect("p115 drive root path", p115_drive.json().get("path"), "")
        expect("p115 drive root outside", p115_drive.json().get("inside_library"), False)
        expect("p115 drive root fid", p115_drive.json().get("fid"), "0")

        p115_mkdir_tree = client.post(
            "/api/subscriptions/p115/tree",
            json={
                "library_id": p115_library_id,
                "fid": "f-root",
                "path": "待整理",
                "name": "新番",
            },
        )
        expect("p115 tree mkdir status", p115_mkdir_tree.status_code, 200)
        expect("p115 tree mkdir created", fake_115.created_ids[-1], ("f-root", "新番"))
        expect(
            "p115 tree mkdir path", p115_mkdir_tree.json().get("path"), "待整理/新番"
        )

        rss_folder_body = {
            "name": "115 目录订阅",
            "library_id": p115_library_id,
            "type": "rss",
            "config": {
                "feed_urls": ["https://mikanani.me/RSS/Test"],
                "p115_folder_id": "f-1",
                "p115_folder_path": "待整理/API",
            },
            "schedule": {"mode": "interval", "interval_minutes": 30},
        }
        rss_folder = client.post("/api/subscriptions", json=rss_folder_body)
        rss_folder_data = rss_folder.json()
        expect("p115 folder create status", rss_folder.status_code, 200)
        rss_folder_id = rss_folder_data.get("id")
        expect(
            "p115 folder mapped subdir", rss_folder_data["config"].get("save_subdir"), "API"
        )
        expect("p115 folder mapped root", rss_folder_data["config"].get("save_root"), False)
        expect(
            "p115 folder id kept", rss_folder_data["config"].get("p115_folder_id"), "f-1"
        )
        expect(
            "p115 outside folder blocked",
            client.post(
                "/api/subscriptions",
                json={
                    **rss_folder_body,
                    "name": "115 库外订阅",
                    "config": {
                        **rss_folder_body["config"],
                        "p115_folder_id": "f-x",
                        "p115_folder_path": "Movies",
                    },
                },
            ).status_code,
            400,
        )
        p115_folder_updated = client.put(
            f"/api/subscriptions/{rss_folder_id}",
            json={
                **rss_folder_body,
                "config": {
                    **rss_folder_body["config"],
                    "p115_folder_id": "f-root",
                    "p115_folder_path": "待整理",
                },
            },
        )
        expect("p115 folder update status", p115_folder_updated.status_code, 200)
        expect(
            "p115 folder update root",
            p115_folder_updated.json()["config"].get("save_root"),
            True,
        )

        # ---------- 夸克分享文件树 ----------
        share_tree = client.post(
            "/api/subscriptions/quark/share/tree",
            json={"share_url": SHARE_URL, "passcode": PASSCODE},
        )
        share_tree_data = share_tree.json()
        share_root_entries = share_tree_data.get("entries", [])
        expect("share tree root status", share_tree.status_code, 200)
        expect(
            "share tree root share id", share_tree_data.get("share_id"), "abc123def456"
        )
        expect("share tree root path", share_tree_data.get("path"), "")
        expect("share tree root fid", share_tree_data.get("fid"), "0")
        expect("share tree root dirs", share_tree_data.get("dirs"), 1)
        expect("share tree root files", share_tree_data.get("files"), 0)
        expect("share tree root truncated", share_tree_data.get("truncated"), False)
        expect(
            "share tree root entries",
            [
                (item["name"], item["is_dir"], item["path"])
                for item in share_root_entries
            ],
            [("Season 1", True, "Season 1")],
        )
        expect(
            "share tree entry keys",
            sorted(share_root_entries[0]) if share_root_entries else [],
            ["fid", "is_dir", "name", "path", "size"],
        )

        season_tree = client.post(
            "/api/subscriptions/quark/share/tree",
            json={"share_url": SHARE_URL, "passcode": PASSCODE, "path": "Season 1"},
        )
        season_data = season_tree.json()
        season_entries = season_data.get("entries", [])
        expect("share tree season status", season_tree.status_code, 200)
        expect("share tree season path", season_data.get("path"), "Season 1")
        expect("share tree season fid", season_data.get("fid"), season_fid)
        expect("share tree season dirs", season_data.get("dirs"), 0)
        expect("share tree season files", season_data.get("files"), 3)
        expect("share tree season truncated", season_data.get("truncated"), False)
        expect(
            "share tree season names",
            [item["name"] for item in season_entries],
            [
                "[DKB] Dragon Ball Super Beerus - S01E01 [1080p][V2].mkv",
                "[DKB] Dragon Ball Super Beerus - S01E02 [1080p][HEVC x265 10bit].mkv",
                "readme.txt",
            ],
        )
        expect(
            "share tree season is_dir",
            [item["is_dir"] for item in season_entries],
            [False, False, False],
        )
        expect(
            "share tree season sizes",
            [item["size"] for item in season_entries],
            [1024, 1024, 1024],
        )
        expect(
            "share tree season paths",
            [item["path"] for item in season_entries],
            [
                "Season 1/[DKB] Dragon Ball Super Beerus - S01E01 [1080p][V2].mkv",
                "Season 1/[DKB] Dragon Ball Super Beerus - S01E02 [1080p][HEVC x265 10bit].mkv",
                "Season 1/readme.txt",
            ],
        )
        expect(
            "share tree entries have no token",
            all("fid_token" not in item and "token" not in item for item in season_entries),
            True,
        )

        expect(
            "share tree wrong passcode",
            client.post(
                "/api/subscriptions/quark/share/tree",
                json={"share_url": SHARE_URL, "passcode": "9999"},
            ).status_code,
            400,
        )
        expect(
            "share tree traversal",
            client.post(
                "/api/subscriptions/quark/share/tree",
                json={"share_url": SHARE_URL, "passcode": PASSCODE, "path": "../etc"},
            ).status_code,
            400,
        )
        missing_share = client.post("/api/subscriptions/quark/share/tree", json={})
        expect("share tree missing share url", missing_share.status_code, 400)
        expect(
            "share tree missing share url message",
            "请填写" in missing_share.json().get("detail", ""),
            True,
        )
        expect(
            "share tree missing dir",
            client.post(
                "/api/subscriptions/quark/share/tree",
                json={"share_url": SHARE_URL, "passcode": PASSCODE, "path": "Nope"},
            ).status_code,
            400,
        )
        expect(
            "share tree inline passcode",
            client.post(
                "/api/subscriptions/quark/share/tree",
                json={"share_url": f"{SHARE_URL}?pwd={PASSCODE}"},
            ).status_code,
            200,
        )

        # passcode 为空时，编辑已有订阅可回退保存的提取码；分享链接不同则不复用。
        fallback_tree = client.post(
            "/api/subscriptions/quark/share/tree",
            json={
                "share_url": SHARE_URL,
                "passcode": "",
                "subscription_id": subscription_id,
            },
        )
        expect("share tree passcode fallback", fallback_tree.status_code, 200)
        expect(
            "share tree fallback entries",
            [item["name"] for item in fallback_tree.json().get("entries", [])],
            ["Season 1"],
        )
        expect(
            "share tree other share no fallback",
            client.post(
                "/api/subscriptions/quark/share/tree",
                json={
                    "share_url": "https://pan.quark.cn/s/other999xyz",
                    "passcode": "",
                    "subscription_id": subscription_id,
                },
            ).status_code,
            400,
        )
        expect(
            "share tree unknown subscription no fallback",
            client.post(
                "/api/subscriptions/quark/share/tree",
                json={
                    "share_url": SHARE_URL,
                    "passcode": "",
                    "subscription_id": 9999,
                },
            ).status_code,
            400,
        )

        # 文件夹优先、文件按名称排序，条目超过 1000 时截断但计数完整。
        bulk_fid = server.state.add_folder("Bulk", fid="bulk-dir")
        for index in range(1005):
            server.state.add_file(
                f"item-{index:04d}.mkv", parent=bulk_fid, fid=f"bulk-{index}"
            )
        bulk_tree = client.post(
            "/api/subscriptions/quark/share/tree",
            json={"share_url": SHARE_URL, "passcode": PASSCODE, "path": "Bulk"},
        )
        bulk_data = bulk_tree.json()
        expect("share tree cap status", bulk_tree.status_code, 200)
        expect("share tree cap entries", len(bulk_data.get("entries", [])), 1000)
        expect("share tree cap truncated", bulk_data.get("truncated"), True)
        expect("share tree cap dirs", bulk_data.get("dirs"), 0)
        expect("share tree cap files", bulk_data.get("files"), 1005)

        server.state.add_file("a-root-file.mkv", fid="root-a")
        server.state.add_file("z-root-file.mkv", fid="root-z")
        mixed_tree = client.post(
            "/api/subscriptions/quark/share/tree",
            json={"share_url": SHARE_URL, "passcode": PASSCODE},
        )
        mixed_data = mixed_tree.json()
        expect("share tree mixed status", mixed_tree.status_code, 200)
        expect("share tree mixed dirs", mixed_data.get("dirs"), 2)
        expect("share tree mixed files", mixed_data.get("files"), 2)
        expect(
            "share tree dirs before files",
            [item["name"] for item in mixed_data.get("entries", [])],
            ["Bulk", "Season 1", "a-root-file.mkv", "z-root-file.mkv"],
        )

        # ---------- 分享文件树按目录 ID 直接打开 ----------
        tree_by_fid = client.post(
            "/api/subscriptions/quark/share/tree",
            json={
                "share_url": SHARE_URL,
                "passcode": PASSCODE,
                "fid": season_fid,
            },
        )
        tree_by_fid_data = tree_by_fid.json()
        expect("share tree by fid status", tree_by_fid.status_code, 200)
        expect("share tree by fid path", tree_by_fid_data.get("path"), "Season 1")
        expect("share tree by fid current", tree_by_fid_data.get("fid"), season_fid)
        expect(
            "share tree by fid has entries",
            bool(tree_by_fid_data.get("entries")),
            True,
        )
        expect(
            "share tree by bad fid",
            client.post(
                "/api/subscriptions/quark/share/tree",
                json={
                    "share_url": SHARE_URL,
                    "passcode": PASSCODE,
                    "fid": "not-found",
                },
            ).status_code,
            400,
        )

        # ---------- 分享链接目录 ID 解析（quark-auto-save 风格 #/list/share/<fid>） ----------
        resolved = client.post(
            "/api/subscriptions/quark/share/resolve",
            json={"share_url": SHARE_URL, "passcode": PASSCODE, "fid": season_fid},
        )
        expect("share resolve status", resolved.status_code, 200)
        expect("share resolve path", resolved.json().get("path"), "Season 1")
        expect("share resolve fid", resolved.json().get("fid"), season_fid)
        resolved_root = client.post(
            "/api/subscriptions/quark/share/resolve",
            json={"share_url": SHARE_URL, "passcode": PASSCODE, "fid": "0"},
        )
        expect("share resolve root status", resolved_root.status_code, 200)
        expect("share resolve root path", resolved_root.json().get("path"), "")
        expect(
            "share resolve missing fid",
            client.post(
                "/api/subscriptions/quark/share/resolve",
                json={
                    "share_url": SHARE_URL,
                    "passcode": PASSCODE,
                    "fid": "not-found",
                },
            ).status_code,
            400,
        )
        expect(
            "share resolve saved passcode",
            client.post(
                "/api/subscriptions/quark/share/resolve",
                json={
                    "share_url": SHARE_URL,
                    "passcode": "",
                    "fid": season_fid,
                    "subscription_id": subscription_id,
                },
            ).json().get("path"),
            "Season 1",
        )
        expect(
            "share resolve missing url",
            client.post(
                "/api/subscriptions/quark/share/resolve",
                json={"passcode": PASSCODE, "fid": season_fid},
            ).status_code,
            400,
        )

        # ---------- 运行记录 ----------
        run_id = execute(
            "INSERT INTO subscription_runs(subscription_id,status,trigger,progress,total,done,"
            "stats,message,created_at,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                subscription_id,
                "success",
                "manual",
                100,
                2,
                2,
                dumps({"organized": 2}),
                "整理 2",
                now(),
                now(),
                now(),
            ),
        )
        execute(
            "INSERT INTO subscription_run_logs(run_id,ts,level,message) VALUES(?,?,?,?)",
            (run_id, now(), "info", "测试日志"),
        )
        run_detail = client.get(f"/api/subscriptions/runs/{run_id}")
        expect("run detail status", run_detail.status_code, 200)
        expect("run detail name", run_detail.json()["subscription_name"], "夸克测试改名")
        expect("run detail logs", len(run_detail.json()["logs"]), 1)
        expect("run detail stats", run_detail.json()["stats"], {"organized": 2})
        expect("run missing", client.get("/api/subscriptions/runs/9999").status_code, 404)
        subscription_runs = client.get(
            f"/api/subscriptions/{subscription_id}/runs"
        )
        expect("subscription runs", len(subscription_runs.json()), 1)
        all_runs = client.get("/api/subscriptions/runs")
        expect("all runs", any(row["id"] == run_id for row in all_runs.json()), True)

        active_run = execute(
            "INSERT INTO subscription_runs(subscription_id,status,trigger,created_at) "
            "VALUES(?,?,?,?)",
            (subscription_id, "running", "manual", now()),
        )
        expect(
            "trigger while running",
            client.post(f"/api/subscriptions/{subscription_id}/run").status_code,
            409,
        )
        expect(
            "delete while running",
            client.delete(f"/api/subscriptions/{subscription_id}").status_code,
            409,
        )
        expect(
            "trigger missing",
            client.post("/api/subscriptions/9999/run").status_code,
            404,
        )
        execute("UPDATE subscription_runs SET status='failed' WHERE id=?", (active_run,))
        expect(
            "delete subscription",
            client.delete(f"/api/subscriptions/{subscription_id}").status_code,
            200,
        )
        expect(
            "deleted subscription gone",
            all(
                item["id"] != subscription_id
                for item in client.get("/api/subscriptions").json()
            ),
            True,
        )
        expect(
            "delete missing",
            client.delete(f"/api/subscriptions/{subscription_id}").status_code,
            404,
        )

        # 清理其余订阅，避免后台调度器在测试结束前触发它们。
        client.delete(f"/api/subscriptions/{duplicate_id}")
        client.delete(f"/api/subscriptions/{rss_id}")
        client.delete(f"/api/subscriptions/{blank_id}")
        client.delete(f"/api/subscriptions/{legacy_id}")
        client.delete(f"/api/subscriptions/{renamed_subscription_id}")

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
