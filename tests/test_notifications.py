"""通知渠道配置加密、事件过滤与 API 回归测试。

运行：cd backend && .venv/bin/python ../tests/test_notifications.py
使用临时数据目录，不发送真实网络请求。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cinemint-notifications-")
os.environ["CINEMINT_DATA_DIR"] = TEMP_DATA
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from fastapi.testclient import TestClient  # noqa: E402

from app.core import notifications  # noqa: E402
from app.db import query_all, query_one  # noqa: E402
from app.main import app  # noqa: E402


WEBHOOK_CONFIG = {
    "url": "https://example.com/hook?token=abc",
    "method": "POST",
    "content_type": "application/json",
    "headers": "",
    "body": "",
}


def main() -> int:
    failures: list[str] = []

    if len(notifications.CHANNEL_CATALOG) != 23:
        failures.append(f"catalog size {len(notifications.CHANNEL_CATALOG)}")
    for item in notifications.CHANNEL_CATALOG:
        if item["type"] not in notifications.ADAPTERS:
            failures.append(f"adapter missing: {item['type']}")

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cinemint"})
        if login.status_code != 200:
            failures.append(f"login {login.status_code}")
            print("\n".join(f"FAIL {item}" for item in failures))
            return 1

        catalog = client.get("/api/notifications/catalog")
        if catalog.status_code != 200 or len(catalog.json()["channels"]) != 23:
            failures.append("catalog API wrong")
        if "run.summary" not in {item["key"] for item in catalog.json()["events"]}:
            failures.append("events missing run.summary")

        created = client.post(
            "/api/notifications",
            json={
                "name": "测试 Webhook",
                "type": "webhook",
                "config": WEBHOOK_CONFIG,
                "events": ["run.summary"],
                "enabled": True,
            },
        )
        if created.status_code != 200:
            failures.append(f"create channel: {created.status_code} {created.text}")
            print("\n".join(f"FAIL {item}" for item in failures))
            return 1
        channel = created.json()
        channel_id = channel["id"]
        if channel["config"]["url"] != "":
            failures.append("public config leaks webhook URL")
        if "url" not in channel["secret_fields_set"]:
            failures.append("secret marker missing")

        stored = query_one("SELECT config FROM notification_channels WHERE id=?", (channel_id,))
        raw = stored["config"] if stored else ""
        if "example.com" in raw or "enc:" not in raw:
            failures.append("webhook URL not encrypted at rest")

        # 只改名称时保留已存密钥
        updated = client.put(
            f"/api/notifications/{channel_id}", json={"name": "改名"}
        )
        if updated.status_code != 200 or updated.json()["name"] != "改名":
            failures.append("rename channel failed")
        decoded = notifications.decode_config(
            "webhook",
            json.loads(query_one("SELECT config FROM notification_channels WHERE id=?", (channel_id,))["config"]),
        )
        if decoded["url"] != WEBHOOK_CONFIG["url"]:
            failures.append("secret lost on partial update")

        # 事件过滤与发送日志
        calls: list[tuple[str, str, str]] = []
        original = notifications.ADAPTERS["webhook"]

        def fake(config, title, content):
            calls.append((config["url"], title, content))

        notifications.ADAPTERS["webhook"] = fake
        try:
            sent = notifications.send_event("run.summary", "摘要", "内容")
            if sent["sent"] != 1 or len(calls) != 1:
                failures.append(f"event send wrong: {sent}")
            skipped = notifications.send_event("transfer.success", "转存", "内容")
            if skipped["sent"] != 0:
                failures.append("unsubscribed event was sent")
            failed = notifications.send_event("run.summary", "摘要", "内容")
            if failed["sent"] != 1:
                failures.append("second send failed")

            # 合并推送：渠道只勾选「无法识别」时，运行摘要里包含该类内容也要收到；
            # 不传 match_events 的旧调用仍按单一事件精确过滤。
            client.put(
                f"/api/notifications/{channel_id}",
                json={"events": ["item.unmatched"]},
            )
            aggregated = notifications.send_event(
                "run.summary",
                "摘要",
                "内容",
                match_events=["run.summary", "item.unmatched"],
            )
            if aggregated["sent"] != 1:
                failures.append(f"aggregated match_events not sent: {aggregated}")
            irrelevant = notifications.send_event(
                "run.summary",
                "摘要",
                "内容",
                match_events=["organize.success"],
            )
            if irrelevant["sent"] != 0:
                failures.append("aggregated message sent without matching event")
            legacy = notifications.send_event("run.summary", "摘要", "内容")
            if legacy["sent"] != 0:
                failures.append("legacy filtering changed")
            direct = notifications.send_event("item.unmatched", "无法识别", "内容")
            if direct["sent"] != 1:
                failures.append("event-specific send failed")
            client.put(
                f"/api/notifications/{channel_id}",
                json={"events": ["run.summary"]},
            )

            test = client.post(f"/api/notifications/{channel_id}/test")
            if test.status_code != 200:
                failures.append(f"saved channel test: {test.status_code} {test.text}")
            unsaved = client.post(
                "/api/notifications/test",
                json={"type": "webhook", "config": WEBHOOK_CONFIG},
            )
            if unsaved.status_code != 200:
                failures.append(f"unsaved test: {unsaved.status_code} {unsaved.text}")

            notifications.ADAPTERS["webhook"] = lambda *args: (_ for _ in ()).throw(
                notifications.NotificationError("模拟失败")
            )
            failed_test = client.post(f"/api/notifications/{channel_id}/test")
            if failed_test.status_code != 400:
                failures.append("failed test should return 400")
            row = query_one(
                "SELECT last_test_ok, last_error FROM notification_channels WHERE id=?",
                (channel_id,),
            )
            if not row or row["last_test_ok"] != 0 or "模拟失败" not in (row["last_error"] or ""):
                failures.append("failed test state not recorded")
        finally:
            notifications.ADAPTERS["webhook"] = original

        logs = client.get("/api/notifications/logs", params={"channel_id": channel_id})
        if logs.status_code != 200 or not logs.json()["logs"]:
            failures.append("delivery logs missing")
        if query_one("SELECT COUNT(*) AS c FROM notification_logs WHERE ok=0")["c"] < 1:
            failures.append("failure log missing")

        removed = client.delete(f"/api/notifications/{channel_id}")
        if removed.status_code != 200:
            failures.append("delete channel failed")
        if query_all("SELECT id FROM notification_channels WHERE id=?", (channel_id,)):
            failures.append("channel still exists")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("notification cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
