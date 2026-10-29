"""合并推送回归：同一批次的多个任务只发一条「影序自动更新」。

覆盖：
- 两个任务的结果合并成 1 条消息，标题固定为「影序自动更新」；
- 消息里按任务分段，包含任务名、统计、分类标题、文件名与提示；
- 通知渠道不同的任务各自成组，互不串台；
- 失败任务与成功任务写在同一条消息里；
- 没有新内容时不发送；
- 条目过多时截断并提示，内容不超过上限；
- 一段时间内没有新任务后自动发出。

运行：cd backend && .venv/bin/python ../tests/test_notification_aggregate.py
不访问数据库和网络。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["CINEMINT_DATA_DIR"] = tempfile.mkdtemp(prefix="cinemint-notify-agg-")
os.environ["CINEMINT_DEMO"] = "false"

import app.core.update_digest as update_digest  # noqa: E402
from app.core.subscriptions import RunNotifier  # noqa: E402
from app.core.update_digest import UpdateDigest, build_content  # noqa: E402


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

    original = update_digest.send_event
    update_digest.send_event = fake_send
    try:
        alpha = {
            "id": 7,
            "name": "任务甲",
            "type": "quark",
            "notify_channel_ids": "[3]",
        }
        notifier = RunNotifier(alpha)
        for index in range(1, 4):
            notifier.add("item.unmatched", f"Mystery.Show.S01E0{index}.mkv")
        notifier.add("item.review", "Review.Show.S01E01.mkv")
        notifier.add("transfer.success", "Mystery.Show.S01E01.mkv")
        notifier.add("organize.success", "Mystery.Show.S01E01.mkv")
        notifier.flush(
            "转存 1，整理 1，未识别 3，待确认 1",
            {
                "transferred": 1,
                "organized": 1,
                "unmatched": 3,
                "review": 1,
            },
        )
        beta = {
            "id": 8,
            "name": "任务乙",
            "type": "rss",
            "notify_channel_ids": "[3]",
        }
        second = RunNotifier(beta)
        second.add("transfer.success", "Other.Show.S02E01.mkv")
        second.add("organize.success", "Other.Show.S02E01.mkv")
        second.flush("转存 1，整理 1", {"transferred": 1, "organized": 1})
        expect("two reports pending", update_digest.digest.pending(), 2)

        update_digest.digest.flush()
        expect("single send for batch", len(captured), 1)
        message = captured[0]
        expect("event", message["event"], "run.summary")
        expect("title", message["title"], "影序自动更新")
        expect("channels", message["channel_ids"], [3])
        content = message["content"]
        expect("task count header", content.startswith("共 2 个任务有更新"), True)
        expect("first task block", "【任务甲】" in content, True)
        expect("second task block", "【任务乙】" in content, True)
        expect("time last", content.splitlines()[-1].startswith("时间："), True)
        expect(
            "task counts",
            "转存成功 1 · 失败 0 · 整理 1 · 未识别 3 · 待确认 1" in content,
            True,
        )
        expect(
            "each episode on its own line",
            "\n- Mystery.Show.S01E01.mkv" in content
            and "\n- Mystery.Show.S01E02.mkv" in content
            and "\n- Mystery.Show.S01E03.mkv" in content,
            True,
        )
        for text in (
            "Review.Show.S01E01.mkv",
            "Other.Show.S02E01.mkv",
            "【无法识别 · 3】",
            "【待确认 · 1】",
            "【转存完成 · 1】",
            "【整理完成 · 1】",
        ):
            expect(f"content has {text}", text in content, True)
        expect("unmatched hint", "手动匹配后，下次更新的剧集" in content, True)
        expect(
            "match events cover sections",
            {"item.unmatched", "item.review", "transfer.success", "organize.success"}
            <= set(message["match_events"]),
            True,
        )
        expect("run.summary in match events", "run.summary" in message["match_events"], True)

        # 重复 flush 不再发送。
        update_digest.digest.flush()
        expect("flush idempotent", len(captured), 1)

        # 失败任务进入同一条消息，并在头部标注失败数量。
        captured.clear()
        from app.core import subscriptions  # noqa: E402  (延迟导入，避免影响补丁)

        subscriptions._notify(alpha, "run.failed", "运行失败", "夸克分享不可用: 测试错误")
        update_digest.digest.flush()
        failed_content = captured[-1]["content"]
        expect("failed title", captured[-1]["title"], "影序自动更新")
        expect("failed task block", "【任务甲】" in failed_content, True)
        expect("failed kind line", "\n运行失败\n" in failed_content, True)
        expect("failed detail", "夸克分享不可用: 测试错误" in failed_content, True)
        expect("failed event in match events", "run.failed" in captured[-1]["match_events"], True)
        expect("failed header", failed_content.startswith("共 1 个任务，1 个失败"), True)

        # 没有新内容（定时检查、无新文件）不发送。
        captured.clear()
        empty = RunNotifier({"id": 9, "name": "空订阅", "type": "rss"})
        empty.flush("没有发现需要处理的文件", {})
        expect("empty run not queued", update_digest.digest.pending(), 0)
        update_digest.digest.flush()
        expect("empty run not sent", len(captured), 0)
        empty.flush("没有发现需要处理的文件", {}, force=True)
        update_digest.digest.flush()
        expect("forced run sent", len(captured), 1)
        forced_content = captured[-1]["content"]
        expect(
            "forced counts",
            "转存成功 0 · 失败 0 · 整理 0 · 未识别 0 · 待确认 0" in forced_content,
            True,
        )
        expect("forced note", "没有发现需要处理的文件" in forced_content, True)
        expect("forced time last", forced_content.splitlines()[-1].startswith("时间："), True)

        # 条目过多时每个任务只展示前 8 个并在末尾提示。
        captured.clear()
        many = RunNotifier({"id": 10, "name": "大量未识别", "type": "quark"})
        for index in range(25):
            many.add("item.unmatched", f"Many.Show.S01E{index:02d}.mkv")
        many.flush("未识别 25", {"unmatched": 25})
        update_digest.digest.flush()
        many_content = captured[-1]["content"]
        expect("many header count", "【无法识别 · 25】" in many_content, True)
        expect("many shows first", "Many.Show.S01E00.mkv" in many_content, True)
        expect("many hides eighth", "Many.Show.S01E08.mkv" in many_content, False)
        expect("many truncation hint", "…等 25 个" in many_content, True)
        expect("many length cap", len(many_content) <= 3800, True)
        expect("many single task no header", many_content.startswith("【大量未识别】"), True)

        # 渠道不同的任务分组发送，各渠道只收到自己那组。
        captured.clear()
        three = RunNotifier(
            {"id": 12, "name": "渠道三", "type": "quark", "notify_channel_ids": "[3]"}
        )
        three.add("transfer.success", "Channel.Three.S01E01.mkv")
        three.flush("转存 1", {"transferred": 1})
        gamma = RunNotifier(
            {"id": 11, "name": "渠道五", "type": "quark", "notify_channel_ids": "[5]"}
        )
        gamma.add("transfer.success", "Channel.Five.S01E01.mkv")
        gamma.flush("转存 1", {"transferred": 1})
        update_digest.digest.flush()
        expect("channel groups", len(captured), 2)
        by_channel = {tuple(item["channel_ids"] or ()): item for item in captured}
        expect("channel five group", "【渠道五】" in by_channel[(5,)]["content"], True)
        expect("channel three group", "【渠道五】" in by_channel[(3,)]["content"], False)
        expect("channel three content", "【渠道三】" in by_channel[(3,)]["content"], True)

        # 一段时间没有新任务后自动发出，不需要手动 flush。
        captured.clear()
        auto = UpdateDigest(quiet_seconds=0.2, max_wait_seconds=5.0, poll_seconds=0.2)
        auto.submit(
            {
                "name": "自动发送",
                "status": "success",
                "stats": {"transferred": 1},
                "sections": {"transfer.success": ["Auto.Show.S01E01.mkv"]},
                "events": {"transfer.success"},
                "channel_ids": None,
            }
        )
        deadline = time.time() + 5
        while not captured and time.time() < deadline:
            time.sleep(0.05)
        expect("auto flush", len(captured), 1)
        expect("auto title", captured[-1]["title"], "影序自动更新")
        expect("auto no channel filter", captured[-1]["channel_ids"], None)

        # 多任务 header 中的失败计数。
        text = build_content(
            [
                {
                    "name": "好任务",
                    "status": "success",
                    "stats": {"transferred": 2},
                    "sections": {"transfer.success": ["a.mkv", "b.mkv"]},
                },
                {
                    "name": "坏任务",
                    "status": "failed",
                    "kind": "运行失败",
                    "note": "网络不可用",
                },
            ]
        )
        expect("mixed header", text.startswith("共 2 个任务，1 个失败"), True)
        expect("mixed failed block", "【坏任务】\n运行失败\n网络不可用" in text, True)
    finally:
        update_digest.send_event = original

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
