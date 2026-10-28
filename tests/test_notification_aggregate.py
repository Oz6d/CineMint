"""合并推送回归：一次运行只发一条结构化消息。

覆盖：
- 3 个无法识别 + 1 个待确认合并成 1 条消息，而不是 4 条；
- 消息包含订阅名、统计、分类标题、每个文件名与提示；
- 多次调用 flush 不会重复发送；
- 没有新内容时不发送；
- 条目过多时截断并提示，内容不超过上限。

运行：cd backend && .venv/bin/python ../tests/test_notification_aggregate.py
不访问数据库和网络。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["CINEMINT_DATA_DIR"] = tempfile.mkdtemp(prefix="cinemint-notify-agg-")
os.environ["CINEMINT_DEMO"] = "false"

import app.core.subscriptions as subscriptions  # noqa: E402
from app.core.subscriptions import RunNotifier  # noqa: E402


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

    captured: list[dict] = []

    def fake_send(event, title, content, *, channel_ids=None, match_events=None):
        captured.append(
            {
                "event": event,
                "title": title,
                "content": content,
                "channel_ids": channel_ids,
                "match_events": match_events or [],
            }
        )
        return {"sent": 1, "failed": 0, "results": []}

    original = subscriptions.send_event
    subscriptions.send_event = fake_send
    try:
        subscription = {
            "id": 7,
            "name": "测试订阅",
            "type": "quark",
            "notify_channel_ids": "[3]",
        }
        notifier = RunNotifier(subscription)
        for index in range(1, 4):
            notifier.add("item.unmatched", f"Mystery.Show.S01E0{index}.mkv")
        notifier.add("item.review", "Review.Show.S01E01.mkv")
        notifier.add("item.duplicate", "Same.Show.S01E01.mkv：已整理过同集")
        notifier.add("item.replaced", "Same.Show.S01E02.V2.mkv（V2）← 旧版本")
        notifier.add("transfer.failed", "Broken.Show.S01E01.mkv：转存失败")
        notifier.add("transfer.success", "Mystery.Show.S01E01.mkv（复用）")
        notifier.add("organize.success", "Mystery.Show.S01E01.mkv")
        notifier.add("organize.failed", "Broken.Show.S01E01.mkv：整理失败")
        notifier.flush(
            "转存 3，复用 1，整理 1，未识别 3，待确认 1，重复 1，失败 2",
            {
                "transferred": 1,
                "adopted": 1,
                "organized": 1,
                "unmatched": 3,
                "review": 1,
                "duplicate": 1,
                "failed": 2,
                "replaced": 1,
            },
        )
        expect("single send", len(captured), 1)
        message = captured[0]
        expect("event", message["event"], "run.summary")
        expect("title", message["title"], "测试订阅")
        expect("title has no prefix", "影序" not in message["title"], True)
        expect("channels", message["channel_ids"], [3])
        content = message["content"]
        expect("counts first", content.startswith("转存成功 1（复用 1） · 失败 2 · 整理 1 · 未识别 3 · 待确认 1"), True)
        expect("counts before details", content.index("转存成功") < content.index("【无法识别 · 3】"), True)
        expect("time last", content.splitlines()[-1].startswith("时间："), True)
        expect("no run summary wording", "运行摘要" in content, False)
        expect("no header", "【影序" in content, False)
        for text in (
            "Mystery.Show.S01E01.mkv",
            "Mystery.Show.S01E02.mkv",
            "Mystery.Show.S01E03.mkv",
            "Review.Show.S01E01.mkv",
            "Same.Show.S01E01.mkv",
            "Same.Show.S01E02.V2.mkv",
            "Broken.Show.S01E01.mkv",
            "【无法识别 · 3】",
            "【待确认 · 1】",
            "【重复 · 1】",
            "【V2 替换 · 1】",
            "【转存失败 · 1】",
            "【转存完成 · 1】",
            "【整理完成 · 1】",
            "【整理失败 · 1】",
        ):
            expect(f"content has {text}", text in content, True)
        expect(
            "unmatched hint",
            "手动匹配后，下次更新的剧集" in content,
            True,
        )
        expect(
            "match events cover sections",
            {"item.unmatched", "item.review", "transfer.success", "organize.success"}
            <= set(message["match_events"]),
            True,
        )
        expect("run.summary in match events", "run.summary" in message["match_events"], True)

        # 重复 flush 不再发送。
        notifier.flush("再次摘要", {})
        expect("flush idempotent", len(captured), 1)

        # run.failed 这类单独通知：标题同样只用任务名，失败类型移到正文。
        subscriptions._notify(
            subscription,
            "run.failed",
            "运行失败",
            "夸克分享不可用: 测试错误",
        )
        failed_message = captured[-1]
        expect("failed title no prefix", failed_message["title"], "测试订阅")
        expect(
            "failed body starts with type",
            failed_message["content"].startswith("运行失败\n"),
            True,
        )
        expect("failed no app prefix", "影序" not in failed_message["title"], True)

        # 没有新内容（定时检查、无新文件）不发送。
        empty = RunNotifier({"id": 8, "name": "空订阅", "type": "rss"})
        empty.flush("没有发现需要处理的文件", {})
        expect("empty run not sent", len(captured), 2)
        empty.flush("没有发现需要处理的文件", {}, force=True)
        expect("forced run sent", len(captured), 3)
        forced_content = captured[-1]["content"]
        expect("forced counts", forced_content.startswith("转存成功 0 · 失败 0 · 整理 0 · 未识别 0 · 待确认 0"), True)
        expect("forced time last", forced_content.splitlines()[-1].startswith("时间："), True)

        # 条目过多时截断，只展示前 15 个并在末尾提示。
        many = RunNotifier({"id": 9, "name": "大量未识别", "type": "quark"})
        for index in range(25):
            many.add("item.unmatched", f"Many.Show.S01E{index:02d}.mkv")
        many.flush("未识别 25", {"unmatched": 25})
        expect("many single send", len(captured), 4)
        many_content = captured[-1]["content"]
        expect("many header count", "【无法识别 · 25】" in many_content, True)
        expect("many shows first", "Many.Show.S01E00.mkv" in many_content, True)
        expect("many hides late", "Many.Show.S01E24.mkv" in many_content, False)
        expect("many truncation hint", "…等 25 个" in many_content, True)
        expect("many length cap", len(many_content) <= 3800, True)
    finally:
        subscriptions.send_event = original

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
