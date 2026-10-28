"""持久化订阅调度器。

按 subscriptions.next_run_at 触发，运行记录写入 subscription_runs。
执行器由订阅模块注册；未注册时调度器不会领取到期任务。
"""

from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable

from ..db import execute, execute_count, now, query_all, query_one
from .bangumi import sync_follows_for_subscription
from .scheduling import next_run_for_subscription
from .subscription_runs import (
    RUN_STATUSES,
    create_run,
    finish_run,
    log_run,
    recover_interrupted_runs,
    start_run,
)

logger = logging.getLogger("cinemint.scheduler")

RunHandler = Callable[[dict[str, Any], int], dict[str, Any] | None]


def _tasks_busy(library_id: int | None) -> bool:
    if not library_id:
        return False
    row = query_one(
        "SELECT id FROM tasks WHERE library_id=? AND status IN ('queued','running') LIMIT 1",
        (library_id,),
    )
    return bool(row)


class SubscriptionScheduler:
    def __init__(self, tick_seconds: float = 15.0) -> None:
        self._tick_seconds = max(1.0, float(tick_seconds))
        self._handler: RunHandler | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active: dict[int, int] = {}
        self._active_lock = threading.Lock()

    def set_handler(self, handler: RunHandler | None) -> None:
        self._handler = handler
        if handler is not None:
            self.ensure_next_runs()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        recover_interrupted_runs()
        self._stop.clear()
        self.ensure_next_runs()
        self._thread = threading.Thread(
            target=self._loop, name="cinemint-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def ensure_next_runs(self) -> None:
        for subscription in query_all(
            "SELECT * FROM subscriptions WHERE enabled=1 AND next_run_at IS NULL"
        ):
            next_at = next_run_for_subscription(subscription)
            if next_at:
                execute(
                    "UPDATE subscriptions SET next_run_at=?, updated_at=? WHERE id=?",
                    (next_at, now(), subscription["id"]),
                )

    def trigger(self, subscription_id: int, trigger: str = "manual") -> int | None:
        """立即运行一次；已有运行或任务占用时返回 None。"""
        subscription = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
        if not subscription:
            raise ValueError("订阅不存在")
        with self._active_lock:
            if subscription_id in self._active:
                return None
            if _tasks_busy(subscription.get("library_id")):
                return None
            existing = query_one(
                "SELECT id FROM subscription_runs WHERE subscription_id=? "
                "AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
                (subscription_id,),
            )
            if existing:
                return None
            run_id = create_run(subscription_id, trigger=trigger)
            self._active[subscription_id] = run_id
            next_at = next_run_for_subscription(subscription)
            execute(
                "UPDATE subscriptions SET next_run_at=?, updated_at=? WHERE id=?",
                (next_at, now(), subscription_id),
            )
        self._spawn(subscription_id, run_id)
        return run_id

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("订阅调度器 tick 失败")
            self._stop.wait(self._tick_seconds)

    def _tick(self) -> None:
        if self._handler is None:
            return
        due = query_all(
            "SELECT * FROM subscriptions WHERE enabled=1 AND next_run_at IS NOT NULL "
            "AND next_run_at<=? ORDER BY next_run_at LIMIT 5",
            (now(),),
        )
        for subscription in due:
            if self._stop.is_set():
                return
            self._claim_and_start(subscription)

    def _reschedule(self, subscription: dict[str, Any]) -> None:
        next_at = next_run_for_subscription(subscription)
        execute(
            "UPDATE subscriptions SET next_run_at=?, updated_at=? WHERE id=?",
            (next_at, now(), subscription["id"]),
        )

    def _claim_and_start(self, subscription: dict[str, Any]) -> bool:
        subscription_id = int(subscription["id"])
        with self._active_lock:
            if subscription_id in self._active:
                self._reschedule(subscription)
                return False
            if _tasks_busy(subscription.get("library_id")):
                self._reschedule(subscription)
                return False
            existing = query_one(
                "SELECT id FROM subscription_runs WHERE subscription_id=? "
                "AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
                (subscription_id,),
            )
            if existing:
                self._reschedule(subscription)
                return False
            next_at = next_run_for_subscription(subscription)
            if next_at is None:
                execute(
                    "UPDATE subscriptions SET next_run_at=NULL, updated_at=? WHERE id=?",
                    (now(), subscription_id),
                )
                return False
            claimed = execute_count(
                "UPDATE subscriptions SET next_run_at=?, updated_at=? "
                "WHERE id=? AND next_run_at=?",
                (next_at, now(), subscription_id, subscription["next_run_at"]),
            )
            if claimed != 1:
                return False
            run_id = create_run(subscription_id, trigger="schedule")
            self._active[subscription_id] = run_id
        self._spawn(subscription_id, run_id)
        return True

    def _spawn(self, subscription_id: int, run_id: int) -> None:
        thread = threading.Thread(
            target=self._run,
            args=(subscription_id, run_id),
            name=f"cinemint-subscription-{subscription_id}",
            daemon=True,
        )
        thread.start()

    def _run(self, subscription_id: int, run_id: int) -> None:
        try:
            subscription = query_one(
                "SELECT * FROM subscriptions WHERE id=?", (subscription_id,)
            )
            if not subscription:
                finish_run(run_id, "cancelled", message="订阅已删除")
                return
            if not subscription["enabled"]:
                finish_run(run_id, "cancelled", message="订阅已停用")
                return
            start_run(run_id)
            handler = self._handler
            if handler is None:
                finish_run(run_id, "failed", error="订阅执行器未注册")
                return
            result = handler(subscription, run_id)
            if isinstance(result, dict):
                status = str(result.get("status") or "success")
                if status not in RUN_STATUSES:
                    status = "success"
                finish_run(
                    run_id,
                    status,
                    stats=result.get("stats") if isinstance(result.get("stats"), dict) else {},
                    message=str(result.get("message") or ""),
                    error=result.get("error"),
                )
            else:
                finish_run(run_id, "success", message="运行完成")
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            logger.exception("订阅 %s 运行失败", subscription_id)
            log_run(run_id, f"运行失败: {detail}", "error")
            finish_run(
                run_id,
                "failed",
                message=detail[:500],
                error=f"{detail}\n{traceback.format_exc(limit=4)}",
            )
        finally:
            try:
                sync_follows_for_subscription(subscription_id)
            except Exception:
                logger.exception("同步追更进度失败: 订阅 %s", subscription_id)
            with self._active_lock:
                self._active.pop(subscription_id, None)


scheduler = SubscriptionScheduler()
