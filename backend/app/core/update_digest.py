"""把同一批次的订阅运行合并成一条「影序自动更新」推送。

调度器一次会并发执行多个订阅。如果每个订阅各发一条消息，集中更新时
通知渠道会被刷屏。这里按批次收集运行结果：运行结束先暂存，等所有运行
都结束并且安静一小段时间后，再把这一批任务合并成一条消息发出。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

from .notifications import send_event

logger = logging.getLogger("cinemint.update_digest")

DIGEST_TITLE = "影序自动更新"
# 一条消息里每个任务最多展示的分类；整条消息仍受长度上限约束。
DIGEST_ITEM_LIMIT = 8
DIGEST_TASK_LIMIT = 20
DIGEST_CONTENT_LIMIT = 3800
# 最后一次运行结束后再等这么久，避免同一时段的任务被拆成两条。
DIGEST_QUIET_SECONDS = 20.0
# 兜底：批次再长也要发出去，避免某个任务卡住时消息一直不发。
DIGEST_MAX_WAIT_SECONDS = 600.0

SECTION_ORDER = (
    "item.unmatched",
    "item.review",
    "transfer.failed",
    "item.duplicate",
    "item.skipped",
    "organize.failed",
    "item.replaced",
    "transfer.success",
    "organize.success",
)
SECTION_TITLE = {
    "item.unmatched": "无法识别",
    "item.review": "待确认",
    "transfer.failed": "转存失败",
    "item.duplicate": "重复",
    "item.skipped": "跳过",
    "organize.failed": "整理失败",
    "item.replaced": "V2 替换",
    "transfer.success": "转存完成",
    "organize.success": "整理完成",
}
SECTION_NOTE = {
    "item.unmatched": "请到「影视库 → 待确认」手动匹配；手动匹配后，下次更新的剧集会按学到的标题自动识别，并在订阅开启自动整理时直接整理。",
    "item.review": "请到「影视库 → 待确认」确认识别结果。",
    "item.duplicate": "已存在同集或已整理版本，未重复整理。",
    "item.skipped": "目标已有同名文件，按同名文件策略跳过，源文件仍在暂存目录。",
    "organize.failed": "请到「任务中心」查看失败原因。",
    "item.replaced": "旧版本文件已移入回收目录。",
    "transfer.failed": "请检查夸克 Cookie / 115 授权或稍后重试。",
}


def _local_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def _counts(stats: dict[str, Any] | None) -> str:
    data = stats or {}
    transfer = data.get("transferred")
    if transfer is None:
        transfer = data.get("downloaded", 0)
    transfer = int(transfer or 0)
    adopted = int(data.get("adopted") or 0)
    transfer_text = f"转存成功 {transfer}"
    if adopted:
        transfer_text += f"（复用 {adopted}）"
    return (
        f"{transfer_text} · 失败 {int(data.get('failed') or 0)} · "
        f"整理 {int(data.get('organized') or 0)} · "
        f"未识别 {int(data.get('unmatched') or 0)} · "
        f"待确认 {int(data.get('review') or 0)}"
    )


def _task_lines(report: dict[str, Any]) -> list[str]:
    lines = [f"【{report.get('name') or '订阅'}】"]
    status = str(report.get("status") or "success")
    if status in ("failed", "cancelled"):
        lines.append(str(report.get("kind") or "运行失败"))
        note = str(report.get("note") or "").strip()
        if note:
            lines.extend(note.splitlines())
        return lines
    lines.append(_counts(report.get("stats")))
    note = str(report.get("note") or "").strip()
    if note:
        lines.append(note)
    sections = report.get("sections") or {}
    ordered = list(SECTION_ORDER) + [
        event for event in sections if event not in SECTION_ORDER
    ]
    for event in ordered:
        bucket = sections.get(event) or []
        if not bucket:
            continue
        lines.append("")
        lines.append(f"【{SECTION_TITLE.get(event, event)} · {len(bucket)}】")
        lines.append("")
        for text in bucket[:DIGEST_ITEM_LIMIT]:
            lines.append(f"- {text}")
        if len(bucket) > DIGEST_ITEM_LIMIT:
            lines.append(f"- …等 {len(bucket)} 个，其余请到影序查看")
        section_note = SECTION_NOTE.get(event)
        if section_note:
            lines.append(section_note)
    return lines


def build_content(reports: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    failed = sum(
        1 for item in reports if str(item.get("status") or "success") != "success"
    )
    if failed:
        # 有失败时无论几个任务都给出失败数量，提醒优先看。
        lines.append(f"共 {len(reports)} 个任务，{failed} 个失败")
    elif len(reports) > 1:
        lines.append(f"共 {len(reports)} 个任务有更新")
    for index, report in enumerate(reports[:DIGEST_TASK_LIMIT]):
        if lines:
            lines.append("")
        lines.extend(_task_lines(report))
    if len(reports) > DIGEST_TASK_LIMIT:
        lines.append("")
        lines.append(f"- …等 {len(reports)} 个任务，其余请到影序查看")
    lines.append("")
    lines.append(f"时间：{_local_stamp()}")
    return "\n".join(lines)[:DIGEST_CONTENT_LIMIT]


def _channel_key(report: dict[str, Any]) -> tuple[int, ...]:
    ids = report.get("channel_ids")
    if not ids:
        return ()
    return tuple(sorted({int(item) for item in ids}))


def _report_events(report: dict[str, Any]) -> set[str]:
    events = report.get("events") or set()
    return {str(event) for event in events}


class UpdateDigest:
    """按批次合并订阅运行通知，一批只发一条。"""

    def __init__(
        self,
        quiet_seconds: float = DIGEST_QUIET_SECONDS,
        max_wait_seconds: float = DIGEST_MAX_WAIT_SECONDS,
        poll_seconds: float = 1.0,
    ) -> None:
        self._quiet = max(0.0, float(quiet_seconds))
        self._max_wait = max(self._quiet, float(max_wait_seconds))
        self._poll = max(0.2, float(poll_seconds))
        self._lock = threading.Lock()
        self._reports: list[dict[str, Any]] = []
        self._active = 0
        self._first_at = 0.0
        self._last_at = 0.0
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()

    def run_started(self) -> None:
        with self._lock:
            self._active += 1

    def run_finished(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
        self._wake.set()

    def submit(self, report: dict[str, Any] | None) -> None:
        if not report:
            return
        moment = time.monotonic()
        with self._lock:
            self._reports.append(report)
            if not self._first_at:
                self._first_at = moment
            self._last_at = moment
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._loop, name="cinemint-update-digest", daemon=True
                )
                self._thread.start()
        self._wake.set()

    def pending(self) -> int:
        with self._lock:
            return len(self._reports)

    def flush(self) -> None:
        """立即把已收集的通知合并发出（测试与停机时使用）。"""
        with self._lock:
            reports = self._reports
            self._reports = []
            self._first_at = 0.0
            self._last_at = 0.0
        if not reports:
            return
        groups: dict[tuple[int, ...], list[dict[str, Any]]] = {}
        for report in reports:
            groups.setdefault(_channel_key(report), []).append(report)
        for key, group in groups.items():
            content = build_content(group)
            events = {"run.summary"}
            for report in group:
                events |= _report_events(report)
            try:
                send_event(
                    "run.summary",
                    DIGEST_TITLE,
                    content,
                    channel_ids=list(key) or None,
                    match_events=sorted(events),
                )
            except Exception:
                # 通知失败不能影响订阅本身的执行结果。
                logger.exception("发送合并通知失败")

    def _due(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if not self._reports:
                return False
            span = now - self._first_at
            idle = now - self._last_at
            active = self._active
        if span >= self._max_wait:
            return True
        return active <= 0 and idle >= self._quiet

    def _loop(self) -> None:
        while True:
            self._wake.wait(self._poll)
            self._wake.clear()
            try:
                if self._due():
                    self.flush()
            except Exception:
                logger.exception("合并通知刷新失败")


digest = UpdateDigest()
