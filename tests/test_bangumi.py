"""Bangumi 放送日历与蜜柑追更回归测试。

运行：cd backend && .venv/bin/python ../tests/test_bangumi.py
使用临时数据库、本地模拟 Bangumi API 与 RSS，不访问真实网络。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cinemint-bangumi-")
os.environ["CINEMINT_DATA_DIR"] = TEMP_DATA
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import bangumi as tracker  # noqa: E402
from app.core.bangumi import BangumiError  # noqa: E402
from app.db import dumps, execute, now, query_all, query_one, set_setting  # noqa: E402
from app.main import app  # noqa: E402

BASE_EPISODE_DATE = datetime(2026, 10, 5, 12, 30, tzinfo=timezone.utc)


class MockBangumiServer:
    def __init__(self) -> None:
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                state.handle(self)

            def log_message(self, *_args) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.feed_episode = 3
        self.calendar_hits = 0
        self.feed_hits = 0

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "MockBangumiServer":
        self.thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    @staticmethod
    def item_html(mikan_id: int) -> str:
        title = "示例番剧" if mikan_id == 4063 else f"番剧 {mikan_id}"
        return (
            "<li>"
            f'<span class="js-expand_bangumi" data-src="/poster-{mikan_id}.jpg" '
            f'data-bangumiid="{mikan_id}"></span>'
            f'<a class="an-text" href="/Home/Bangumi/{mikan_id}">'
            f"[SubGroup] {title}</a>"
            "</li>"
        )

    def calendar_html(self) -> bytes:
        labels = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        blocks = []
        for index, label in enumerate(labels, start=1):
            if index == 2:
                # 同一天多行，验证按星期合并而不是只取第一行
                rows = [
                    [4063, 4064],
                    [4065],
                ]
            else:
                rows = [[4070 + index]]
            blocks.append(f'<div class="row">{label}</div>')
            for row in rows:
                blocks.append(
                    '<div class="an-box animated fadeIn"><ul>'
                    + "".join(self.item_html(mikan_id) for mikan_id in row)
                    + "</ul></div>"
                )
                if index == 2 and len(blocks) % 2 == 0:
                    blocks.append('<div class="row an-res-row-frame"></div>')
        blocks.append('<div class="row">剧场版</div>')
        blocks.append(
            '<div class="an-box"><ul>' + self.item_html(4099) + "</ul></div>"
        )
        return ("<!DOCTYPE html><html><body>" + "".join(blocks) + "</body></html>").encode(
            "utf-8"
        )

    def detail_html(self, mikan_id: int) -> bytes:
        title = "示例番剧" if mikan_id == 4063 else f"番剧 {mikan_id}"
        weekday = "星期二" if mikan_id in {4063, 4064} else "星期一"
        bgm_link = (
            '<p class="bangumi-info"><a href="https://bgm.tv/subject/9001">Bangumi</a></p>'
            if mikan_id == 4063
            else ""
        )
        subgroups = ""
        if mikan_id == 4063:
            subgroups = (
                '<div class="subgroup-text" id="583">'
                '<a href="/Home/PublishGroup/583">SubGroup</a>'
                f'<a class="mikan-rss" href="{self.url}/RSS/Bangumi?bangumiId=4063&subgroupid=583">RSS</a>'
                "</div>"
                '<div class="subgroup-text" id="584">'
                '<a href="/Home/PublishGroup/584">OtherSub</a>'
                f'<a class="mikan-rss" href="{self.url}/RSS/Bangumi?bangumiId=4063&subgroupid=584">RSS</a>'
                "</div>"
            )
        return (
            "<!DOCTYPE html><html><body><div class=\"leftbar-container\">"
            '<p class="bangumi-title">[SubGroup] '
            + title
            + f'<a class="mikan-rss" href="{self.url}/RSS/Bangumi?bangumiId={mikan_id}"></a></p>'
            f'<div class="bangumi-poster" style="background-image: url(/subject.jpg)"></div>'
            f'<p class="bangumi-info">放送日期：{weekday}</p>'
            + bgm_link
            + subgroups
            + "</div></body></html>"
        ).encode("utf-8")

    def rss(self) -> bytes:
        items = []
        for episode in range(1, self.feed_episode + 1):
            published = BASE_EPISODE_DATE - timedelta(days=7 * (self.feed_episode - episode))
            items.append(
                "<item>"
                f"<title>[SubGroup] Show Name - {episode:02d} [1080p].mkv</title>"
                "<link>https://bgm.tv/subject/9001</link>"
                f"<guid>ep-{episode}</guid>"
                f'<enclosure url="{self.url}/files/ep{episode}.torrent" '
                'type="application/x-bittorrent" length="1024"/>'
                f"<pubDate>{published.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
                "</item>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            "<title>Mikan Project - [SubGroup] Show Name</title>"
            f"<image><url>{self.url}/poster.jpg</url></image>"
            "<link>https://mikanani.me/</link>"
            + "".join(items)
            + "</channel></rss>"
        ).encode("utf-8")

    def second_rss(self) -> bytes:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            "<title>Mikan Project - [SubGroup] Another Show</title>"
            "<item>"
            "<title>[SubGroup] Another Show - 01 [1080p].mkv</title>"
            "<link>https://mikanani.me/Home/Episode/second</link>"
            "<guid>second-01</guid>"
            f'<enclosure url="{self.url}/files/second.torrent" '
            'type="application/x-bittorrent" length="1024"/>'
            "</item>"
            "</channel></rss>"
        ).encode("utf-8")

    def handle(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        if path == "/":
            self.calendar_hits += 1
            self._send(handler, 200, self.calendar_html(), "text/html")
            return
        if path.startswith("/Home/Bangumi/"):
            try:
                mikan_id = int(path.rsplit("/", 1)[-1])
            except ValueError:
                self._send(handler, 404, b"not found", "text/plain")
                return
            self._send(handler, 200, self.detail_html(mikan_id), "text/html")
            return
        if path == "/v0/subjects/9001":
            payload = {
                "id": 9001,
                "name": "Show Name",
                "name_cn": "示例番剧",
                "date": "2026-10-05",
                "eps": 12,
                "images": {"large": f"{self.url}/subject.jpg"},
                "rating": {"score": 8.4},
                "summary": "简介",
            }
            self._send(handler, 200, json.dumps(payload).encode("utf-8"), "application/json")
            return
        if path == "/v0/subjects/9002":
            self._send(handler, 404, b'{"message":"not found"}', "application/json")
            return
        if path == "/rss/show.xml":
            self.feed_hits += 1
            self._send(handler, 200, self.rss(), "application/rss+xml")
            return
        if path == "/rss/second.xml":
            self._send(handler, 200, self.second_rss(), "application/rss+xml")
            return
        if path == "/rss/third.xml":
            self._send(handler, 200, self.second_rss(), "application/rss+xml")
            return
        if path == "/rss/empty.xml":
            body = (
                '<?xml version="1.0"?><rss version="2.0"><channel>'
                "<title>Empty</title></channel></rss>"
            ).encode("utf-8")
            self._send(handler, 200, body, "application/rss+xml")
            return
        if path == "/rss/error.xml":
            self._send(handler, 500, b"boom", "text/plain")
            return
        self._send(handler, 404, b"not found", "text/plain")

    @staticmethod
    def _send(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


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

    def expect_true(name: str, actual) -> None:
        expect(name, bool(actual), True)

    # ---------- 纯函数 ----------
    expect("normalize_air_time 9:05", tracker.normalize_air_time("9:05"), "09:05")
    expect("normalize_air_time 23:59", tracker.normalize_air_time("23:59"), "23:59")
    expect("normalize_air_time 24:00", tracker.normalize_air_time("24:00"), "")
    expect("normalize_air_time 空", tracker.normalize_air_time(""), "")
    expect(
        "next_air_at 下周一",
        tracker.next_air_at(0, "20:30", after=datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)),
        "2026-10-12T12:30:00+00:00",
    )
    expect(
        "next_air_at 当天未来",
        tracker.next_air_at(0, "23:00", after=datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)),
        "2026-10-05T15:00:00+00:00",
    )
    expect("next_air_at 无星期", tracker.next_air_at(None, "20:30"), None)
    expect(
        "mikan ID 解析",
        tracker._parse_mikan_ids(
            "https://mikanani.me/RSS/Bangumi?bangumiId=321&subgroupid=65"
        ),
        (321, 65),
    )
    expect(
        "非蜜柑主机不解析 ID",
        tracker._parse_mikan_ids("https://example.com/RSS?bangumiId=321&subgroupid=65"),
        (None, None),
    )

    media = Path(tempfile.mkdtemp(prefix="cinemint-bangumi-media-"))
    (media / "待整理").mkdir(parents=True, exist_ok=True)
    (media / "For infuse").mkdir(parents=True, exist_ok=True)

    with MockBangumiServer() as server, TestClient(app) as client:
        feed_url = f"{server.url}/rss/show.xml"
        second_url = f"{server.url}/rss/second.xml"
        parsed = tracker.parse_follow_feed(feed_url)
        expect("RSS 标题清洗", parsed["title"], "Show Name")
        expect("RSS 字幕组", parsed["subgroup_name"], "SubGroup")
        expect("RSS 推断 Bangumi ID", parsed["mikan_bangumi_id"], 9001)
        expect("RSS 最新集", parsed["latest_episode"], 3.0)
        expect("RSS 下一集", parsed["next_episode"], 4.0)
        expect("RSS 星期", parsed["weekday"], 0)
        expect("RSS 放送时间", parsed["air_time"], "20:30")
        expect_true("RSS 首播时间", parsed["next_air_at"])
        expect_true("RSS 海报", parsed["poster"].endswith("/poster.jpg"))

        try:
            tracker.parse_follow_feed(f"{server.url}/rss/error.xml")
            error = ""
        except BangumiError as exc:
            error = str(exc)
        expect("RSS HTTP 错误", error, "HTTP 500")

        try:
            tracker.parse_follow_feed(f"{server.url}/rss/empty.xml")
            error = ""
        except BangumiError as exc:
            error = str(exc)
        expect("RSS 空条目", error, "RSS 中没有条目，无法建立追更记录")

        try:
            tracker.parse_follow_feed("ftp://example.com/feed.xml")
            error = ""
        except BangumiError as exc:
            error = str(exc)
        expect("RSS 协议校验", error, "请填写有效的 RSS 地址")

        login = client.post("/api/auth/login", json={"password": "cinemint"})
        expect("登录", login.status_code, 200)

        updated = client.put(
            "/api/settings",
            json={
                "values": {
                    "bangumi_api_base": server.url,
                    "mikan_api_base": server.url,
                }
            },
        )
        expect("保存 Bangumi API 地址", updated.json().get("bangumi_api_base"), server.url)
        expect("保存蜜柑地址", updated.json().get("mikan_api_base"), server.url)

        source_id = execute(
            "INSERT INTO sources(name,type,config,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("本地测试", "local", dumps({"base_path": str(media)}), now(), now()),
        )
        library_id = execute(
            "INSERT INTO libraries(source_id,name,root_path,target_root,media_type,naming,"
            "scrape,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "测试库",
                "待整理",
                "For infuse",
                "tv",
                "{}",
                dumps({"enabled": False}),
                now(),
                now(),
            ),
        )
        folder_library_id = execute(
            "INSERT INTO libraries(source_id,name,root_path,target_root,media_type,naming,"
            "scrape,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "115 测试库",
                "115/待整理",
                "115/For infuse",
                "tv",
                "{}",
                dumps({"enabled": False}),
                now(),
                now(),
            ),
        )

        # ---------- 放送日历 ----------
        calendar = client.get("/api/bangumi/calendar")
        expect("日历状态", calendar.status_code, 200)
        payload = calendar.json()
        expect("日历 7 天", len(payload["days"]), 7)
        expect("日历首日", payload["days"][0]["weekday"], 0)
        expect("日历首日标签", payload["days"][0]["label"], "周一")
        expect("日历缓存新鲜", payload["stale"], False)
        expect("日历错误为空", payload["error"], "")
        expect("周一只包含当天条目", len(payload["days"][0]["items"]), 1)
        tuesday = payload["days"][1]
        expect("周二跨行合并条目数", len(tuesday["items"]), 3)
        expect("日历条目为蜜柑 ID", tuesday["items"][0]["mikan_id"], 4063)
        expect("周二第二行条目", tuesday["items"][2]["mikan_id"], 4065)
        expect("日历不再混淆 Bangumi ID", tuesday["items"][0]["bangumi_id"], None)
        expect("日历条目未追更", tuesday["items"][0]["followed"], False)
        first_fetched_at = payload["fetched_at"]
        expect_true("日历 fetched_at", first_fetched_at)
        expect("日历请求次数", server.calendar_hits, 1)

        detail = client.get("/api/bangumi/mikan/4063")
        expect("蜜柑详情状态", detail.status_code, 200)
        detail_body = detail.json()
        expect("蜜柑详情真实 Bangumi ID", detail_body["bangumi_id"], 9001)
        expect("蜜柑详情星期", detail_body["weekday"], 1)
        expect("蜜柑详情总集数", detail_body["total_episodes"], 12)
        expect("蜜柑详情字幕组数", len(detail_body["subgroups"]), 2)
        expect("蜜柑详情首选字幕组", detail_body["subgroups"][0]["name"], "SubGroup")
        expect(
            "蜜柑详情字幕组 RSS",
            detail_body["subgroups"][0]["feed_url"],
            f"{server.url}/RSS/Bangumi?bangumiId=4063&subgroupid=583",
        )
        expect(
            "蜜柑详情默认 RSS",
            detail_body["feed_url"],
            f"{server.url}/RSS/Bangumi?bangumiId=4063",
        )

        preview = client.post("/api/bangumi/rss/preview", json={"feed_url": feed_url})
        expect("RSS 预览状态", preview.status_code, 200)
        preview_body = preview.json()
        expect("RSS 预览条数", preview_body["total"], 3)
        expect("RSS 预览截断", preview_body["truncated"], False)
        expect("RSS 预览首条体积", preview_body["items"][0]["size"], 1024)
        rejected = client.post(
            "/api/bangumi/rss/preview",
            json={"feed_url": "https://example.com/feed.xml"},
        )
        expect("非蜜柑 RSS 预览被拒绝", rejected.status_code, 400)

        cached = client.get("/api/bangumi/calendar").json()
        expect("命中缓存 fetched_at", cached["fetched_at"], first_fetched_at)
        expect("命中缓存不再请求", server.calendar_hits, 1)

        forced = client.get("/api/bangumi/calendar?force=true").json()
        expect("强制刷新请求", server.calendar_hits, 2)
        expect("强制刷新新鲜", forced["stale"], False)

        # ---------- 缓存降级 ----------
        client.put("/api/settings", json={"values": {"mikan_api_base": "http://127.0.0.1:1"}})
        stale = client.get("/api/bangumi/calendar?force=true")
        expect("API 失败仍返回缓存", stale.status_code, 200)
        expect("缓存标记过期", stale.json()["stale"], True)
        expect_true("缓存错误信息", stale.json()["error"])
        expect("缓存仍有 7 天", len(stale.json()["days"]), 7)

        set_setting(tracker.CACHE_SETTING, "")
        missing = client.get("/api/bangumi/calendar?force=true")
        expect("无缓存时 API 失败", missing.status_code, 400)
        client.put("/api/settings", json={"values": {"mikan_api_base": server.url}})
        refetched = client.get("/api/bangumi/calendar?force=true")
        expect("恢复后重新缓存", refetched.status_code, 200)
        expect("恢复后新鲜", refetched.json()["stale"], False)

        # ---------- 从日历建立追更（同时创建订阅） ----------
        created = client.post(
            "/api/bangumi/follows",
            json={
                "mikan_bangumi_id": 4063,
                "feed_url": feed_url,
                "library_id": library_id,
                "interval_minutes": 60,
            },
        )
        expect("日历追更状态", created.status_code, 200)
        follow = created.json()
        follow_id = follow["id"]
        expect("日历追更标题", follow["title"], "示例番剧")
        expect("日历追更原名", follow["title_original"], "示例番剧")
        expect("日历追更真实 Bangumi ID", follow["bangumi_id"], 9001)
        expect("日历追更蜜柑 ID", follow["mikan_bangumi_id"], 4063)
        expect("日历追更总集数", follow["total_episodes"], 12)
        expect("日历追更海报", follow["poster"].endswith("/subject.jpg"), True)
        expect("日历追更最新集", follow["latest_episode"], 3.0)
        expect("日历追更下一集", follow["next_episode"], 4.0)
        expect("日历追更星期", follow["weekday"], 1)
        expect("日历追更无放送时间", follow["air_time"], "")
        expect("日历追更初始状态", follow["status"], "following")
        expect_true("添加追更自动创建订阅", follow["subscription_id"])
        expect("追更检查间隔", follow["check_interval_minutes"], 60)

        duplicate = client.post(
            "/api/bangumi/follows",
            json={
                "mikan_bangumi_id": 4063,
                "feed_url": feed_url,
                "library_id": library_id,
            },
        )
        expect("重复添加同番剧返回同一条", duplicate.json()["id"], follow_id)

        follows = client.get("/api/bangumi/follows").json()
        expect("追更列表数量", len(follows["items"]), 1)
        expect("追更计数", follows["counts"]["following"], 1)
        expected_today = datetime.now(ZoneInfo(tracker.DEFAULT_TIMEZONE)).weekday()
        expect("当天星期", follows["today_weekday"], expected_today)

        decorated = client.get("/api/bangumi/calendar").json()
        decorated_item = decorated["days"][1]["items"][0]
        expect("日历标记已追更", decorated_item["followed"], True)
        expect("日历追更 ID", decorated_item["follow_id"], follow_id)

        # ---------- 订阅配置随追更同步 ----------
        subscription_id = follow["subscription_id"]
        subscription = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
        config = json.loads(subscription["config"])
        expect("订阅包含 RSS", config["feed_urls"], [feed_url])
        expect("暂存目录留空保存到根目录", config["save_subdir"], "")
        expect("暂存目录根目录标记", config["save_root"], True)
        expect("订阅默认不自动整理", subscription["auto_organize"], 0)
        expect_true("订阅下次运行", subscription["next_run_at"])
        expect_true("追更创建后立即排队", subscription["next_run_at"] <= now())

        linked = client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={
                "library_id": library_id,
                "interval_minutes": 120,
                "keyword": "1080p",
                "auto_organize": True,
            },
        )
        expect("更新追更状态", linked.status_code, 200)
        linked_body = linked.json()
        expect("更新后仍是同一条", linked_body["id"], follow_id)
        expect("更新后检查间隔", linked_body["check_interval_minutes"], 120)
        expect("更新后复用订阅", linked_body["subscription_id"], subscription_id)
        subscription = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
        config = json.loads(subscription["config"])
        expect("订阅关键词", config["keyword"], "1080p")
        expect("订阅自动整理", subscription["auto_organize"], 1)
        schedule = json.loads(subscription["schedule"])
        expect("订阅间隔", schedule["interval_minutes"], 120)
        expect("订阅调度方式", schedule["mode"], "interval")

        cron_updated = client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={
                "schedule_mode": "cron",
                "cron": "*/30 * * * *",
                "timezone": "Asia/Shanghai",
                "max_per_run": 0,
            },
        )
        expect("切换 cron 状态", cron_updated.status_code, 200)
        cron_body = cron_updated.json()
        expect("cron 调度方式", cron_body["schedule_mode"], "cron")
        expect("cron 无固定间隔", cron_body["check_interval_minutes"], None)
        expect("cron 表达式", cron_body["cron"], "*/30 * * * *")
        subscription = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
        schedule = json.loads(subscription["schedule"])
        config = json.loads(subscription["config"])
        expect("订阅切换 cron", schedule["mode"], "cron")
        expect("订阅 cron 表达式", schedule["cron"], "*/30 * * * *")
        expect("每次下载无限制", config["max_per_run"], 0)

        invalid_cron = client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={"schedule_mode": "cron", "cron": "bad cron"},
        )
        expect("无效 cron", invalid_cron.status_code, 400)

        rss_count = query_one(
            "SELECT COUNT(*) AS n FROM subscriptions WHERE type='rss'"
        )["n"]
        expect("未重复创建订阅", rss_count, 1)

        # ---------- 刷新与进度同步 ----------
        server.feed_episode = 4
        refreshed = client.post(f"/api/bangumi/follows/{follow_id}/refresh")
        expect("单条刷新状态", refreshed.status_code, 200)
        expect("刷新后最新集", refreshed.json()["latest_episode"], 4.0)
        expect("刷新后下一集", refreshed.json()["next_episode"], 5.0)
        expect("刷新后无错误", refreshed.json()["last_error"], "")
        expect_true("刷新后同步时间", refreshed.json()["last_synced_at"])

        all_refreshed = client.post("/api/bangumi/follows/refresh")
        expect("全部刷新状态", all_refreshed.status_code, 200)
        expect("全部刷新统计", all_refreshed.json(), {"checked": 1, "updated": 1, "failed": 0})

        execute(
            "INSERT INTO subscription_entries(subscription_id, remote_key, file_name, episode, "
            "status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (subscription_id, "ep-5", "Show Name - 05.mkv", 5, "organized", now(), now()),
        )
        tracker.sync_follows_for_subscription(subscription_id)
        synced = query_one("SELECT * FROM bangumi_follows WHERE id=?", (follow_id,))
        expect("订阅运行后最新集", synced["latest_episode"], 5.0)
        expect("订阅运行后下一集", synced["next_episode"], 6.0)
        expect("订阅运行后状态", synced["status"], "following")

        execute(
            "INSERT INTO subscription_entries(subscription_id, remote_key, file_name, episode, "
            "status, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (subscription_id, "ep-12", "Show Name - 12.mkv", 12, "organized", now(), now()),
        )
        tracker.sync_follows_for_subscription(subscription_id)
        completed = query_one("SELECT * FROM bangumi_follows WHERE id=?", (follow_id,))
        expect("完结最新集", completed["latest_episode"], 12.0)
        expect("完结下一集", completed["next_episode"], None)
        expect("完结状态", completed["status"], "completed")
        expect("完结下次放送", completed["next_air_at"], None)

        # ---------- 编辑 ----------
        paused = client.put(
            f"/api/bangumi/follows/{follow_id}", json={"status": "paused"}
        )
        expect("暂停状态", paused.json()["status"], "paused")
        paused_explicit = client.put(
            f"/api/bangumi/follows/{follow_id}", json={"status": "completed"}
        )
        expect("显式完成状态", paused_explicit.json()["status"], "completed")

        edited = client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={"weekday": 2, "total_episodes": 24, "status": "following"},
        )
        expect("编辑星期", edited.json()["weekday"], 2)
        expect("编辑星期标签", edited.json()["weekday_label"], "周三")
        expect("编辑后无放送时间", edited.json()["air_time"], "")
        expect("编辑总集数", edited.json()["total_episodes"], 24)
        expect("编辑状态", edited.json()["status"], "following")

        execute("UPDATE bangumi_follows SET total_episodes=NULL WHERE id=?", (follow_id,))
        auto_total = client.put(
            f"/api/bangumi/follows/{follow_id}", json={"status": "following"}
        )
        expect("更新时自动补齐总集数", auto_total.json()["total_episodes"], 12)
        restored = client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={"total_episodes": 24, "status": "following"},
        )
        expect("恢复总集数", restored.json()["total_episodes"], 24)

        second = client.post(
            "/api/bangumi/follows",
            json={
                "feed_url": second_url,
                "title": "另一部番剧",
                "weekday": 3,
                "library_id": library_id,
            },
        ).json()
        expect("手动追更", second["title"], "另一部番剧")
        expect("手动追更无放送时间", second["air_time"], "")
        expect("手动追更星期标签", second["weekday_label"], "周四")
        expect_true("手动追更创建订阅", second["subscription_id"])

        invalid_weekday = client.put(
            f"/api/bangumi/follows/{second['id']}", json={"weekday": 9}
        )
        expect("星期越界", invalid_weekday.status_code, 400)
        invalid_status = client.put(
            f"/api/bangumi/follows/{second['id']}", json={"status": "bogus"}
        )
        expect("状态非法", invalid_status.status_code, 400)
        missing_feed = client.put(
            f"/api/bangumi/follows/{second['id']}", json={"feed_url": ""}
        )
        expect("缺少 RSS 地址", missing_feed.status_code, 400)

        execute("UPDATE bangumi_follows SET feed_url='' WHERE id=?", (second["id"],))
        missing_feed_refresh = client.post(f"/api/bangumi/follows/{second['id']}/refresh")
        expect("无 RSS 刷新", missing_feed_refresh.status_code, 400)
        execute("UPDATE bangumi_follows SET feed_url=? WHERE id=?", (second_url, second["id"]))
        missing_follow = client.post("/api/bangumi/follows/99999/refresh")
        expect("刷新不存在追更", missing_follow.status_code, 404)

        no_feed = client.post("/api/bangumi/follows", json={"title": "无源番剧"})
        expect("缺少 RSS 无法添加", no_feed.status_code, 400)

        # ---------- 追更选择 115 转存目录 ----------
        picked = client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={
                "library_id": folder_library_id,
                "p115_folder_id": "f-new",
                "p115_folder_path": "待整理/新番",
            },
        )
        expect("追更选择转存目录", picked.status_code, 200)
        picked_sub = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
        picked_config = json.loads(picked_sub["config"])
        expect("追更映射相对目录", picked_config["save_subdir"], "新番")
        expect("追更映射非根", picked_config["save_root"], False)
        expect("追更保留目录 ID", picked_config["p115_folder_id"], "f-new")
        expect("追更保留目录路径", picked_config["p115_folder_path"], "待整理/新番")
        expect("追更切换影视库", picked_sub["library_id"], folder_library_id)

        outside_folder = client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={
                "p115_folder_id": "f-other",
                "p115_folder_path": "Movies/新番",
            },
        )
        expect("追更库外目录被拒绝", outside_folder.status_code, 400)

        invalid_folder = client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={"p115_folder_id": "bad id", "p115_folder_path": "待整理/新番"},
        )
        expect("追更目录 ID 非法", invalid_folder.status_code, 400)

        cleared = client.put(
            f"/api/bangumi/follows/{follow_id}", json={"p115_folder_id": ""}
        )
        expect("追更清除目录选择", cleared.status_code, 200)
        cleared_config = json.loads(
            query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))["config"]
        )
        expect("清除后目录 ID 为空", cleared_config["p115_folder_id"], "")
        expect("清除后目录路径为空", cleared_config["p115_folder_path"], "")
        expect("清除后保留相对目录", cleared_config["save_subdir"], "新番")

        root_folder = client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={"p115_folder_id": "f-root", "p115_folder_path": "待整理"},
        )
        expect("追更选择库根目录", root_folder.status_code, 200)
        root_config = json.loads(
            query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))["config"]
        )
        expect("追更库根标记", root_config["save_root"], True)
        expect("追更库根目录为空", root_config["save_subdir"], "")

        third = client.post(
            "/api/bangumi/follows",
            json={
                "feed_url": f"{server.url}/rss/third.xml",
                "title": "目录订阅番剧",
                "library_id": folder_library_id,
                "p115_folder_id": "f-create",
                "p115_folder_path": "待整理/新建",
            },
        )
        expect("追更创建时选择目录", third.status_code, 200)
        third_sub_id = third.json()["subscription_id"]
        third_sub = query_one("SELECT * FROM subscriptions WHERE id=?", (third_sub_id,))
        third_config = json.loads(third_sub["config"])
        expect("追更创建映射相对目录", third_config["save_subdir"], "新建")
        expect("追更创建保留目录 ID", third_config["p115_folder_id"], "f-create")
        expect("追更创建映射影视库", third_sub["library_id"], folder_library_id)
        expect(
            "清理目录订阅追更",
            client.delete(f"/api/bangumi/follows/{third.json()['id']}").status_code,
            200,
        )

        # ---------- 调用失败 ----------
        client.put("/api/settings", json={"values": {"bangumi_api_base": "ftp://invalid"}})
        expect(
            "非法 API 地址回退默认值",
            client.get("/api/settings").json()["bangumi_api_base"],
            "https://api.bgm.tv",
        )
        client.put("/api/settings", json={"values": {"bangumi_api_base": "http://127.0.0.1:1"}})
        client.put(
            f"/api/bangumi/follows/{follow_id}",
            json={"feed_url": "http://127.0.0.1:1/feed.xml"},
        )
        failed_refresh = client.post("/api/bangumi/follows/refresh")
        expect("刷新失败统计", failed_refresh.json(), {"checked": 2, "updated": 1, "failed": 1})
        error_row = query_one("SELECT * FROM bangumi_follows WHERE id=?", (follow_id,))
        expect_true("刷新失败记录错误", error_row["last_error"])
        client.put(f"/api/bangumi/follows/{follow_id}", json={"feed_url": feed_url})
        client.post("/api/bangumi/follows/refresh")
        client.put("/api/settings", json={"values": {"bangumi_api_base": server.url}})

        # ---------- 删除 ----------
        second_sub_id = second["subscription_id"]
        run_id = execute(
            "INSERT INTO subscription_runs(subscription_id, status, trigger, created_at) "
            "VALUES(?,?,?,?)",
            (second_sub_id, "running", "manual", now()),
        )
        busy = client.delete(f"/api/bangumi/follows/{second['id']}")
        expect("运行中禁止删除追更", busy.status_code, 409)
        execute("UPDATE subscription_runs SET status='success' WHERE id=?", (run_id,))
        deleted = client.delete(f"/api/bangumi/follows/{second['id']}")
        expect("删除追更", deleted.json(), {"ok": True})
        expect(
            "删除追更同时删除订阅",
            query_one("SELECT id FROM subscriptions WHERE id=?", (second_sub_id,)),
            None,
        )
        expect_true(
            "保留其他追更的订阅",
            query_one("SELECT id FROM subscriptions WHERE id=?", (subscription_id,)),
        )
        deleted_again = client.delete(f"/api/bangumi/follows/{second['id']}")
        expect("重复删除", deleted_again.status_code, 404)
        remaining = query_all("SELECT * FROM bangumi_follows")
        expect("剩余追更", len(remaining), 1)

    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
