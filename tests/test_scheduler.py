"""订阅调度器状态机回归测试。

运行：cd backend && .venv/bin/python ../tests/test_scheduler.py
使用临时数据目录，不会触发真实库。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cineflow-scheduler-")
os.environ["CINEFLOW_DATA_DIR"] = TEMP_DATA
os.environ["CINEFLOW_DEMO"] = "false"

from app.core.scheduler import SubscriptionScheduler  # noqa: E402
from app.core.subscription_runs import (  # noqa: E402
    create_run,
    log_run,
    recover_interrupted_runs,
)
from app.db import dumps, execute, init_db, now, query_all, query_one  # noqa: E402


def wait_run(run_id: int, timeout: float = 5.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = query_one("SELECT * FROM subscription_runs WHERE id=?", (run_id,))
        if run and run["status"] in {"success", "partial", "failed", "cancelled"}:
            return run
        time.sleep(0.05)
    return query_one("SELECT * FROM subscription_runs WHERE id=?", (run_id,))


def main() -> int:
    init_db()
    execute(
        "INSERT INTO sources(name,type,config,created_at,updated_at) VALUES('local','local','{}',?,?)",
        (now(), now()),
    )
    execute(
        "INSERT INTO libraries(source_id,name,naming,scrape,created_at,updated_at) "
        "VALUES(1,'test','{}','{}',?,?)",
        (now(), now()),
    )
    subscription_id = execute(
        "INSERT INTO subscriptions(name,type,enabled,library_id,config,schedule,next_run_at,"
        "created_at,updated_at) VALUES('sub','quark',1,1,'{}',?,'2020-01-01T00:00:00+00:00',?,?)",
        (
            dumps({"mode": "interval", "interval_minutes": 60, "timezone": "Asia/Shanghai"}),
            now(),
            now(),
        ),
    )

    failures: list[str] = []
    seen: list[tuple[str, int]] = []

    def handler(sub: dict, run_id: int) -> dict:
        seen.append((sub["name"], run_id))
        log_run(run_id, "handler ran")
        return {"status": "success", "stats": {"saved": 1}, "message": "ok"}

    scheduler = SubscriptionScheduler(tick_seconds=1)
    scheduler.set_handler(handler)
    scheduler._tick()

    run = query_one(
        "SELECT * FROM subscription_runs WHERE subscription_id=? ORDER BY id DESC LIMIT 1",
        (subscription_id,),
    )
    if not run:
        failures.append("scheduled run not created")
    else:
        finished = wait_run(run["id"])
        if not finished or finished["status"] != "success":
            failures.append(f"scheduled run status: {finished and finished['status']}")
        if finished and "saved" not in (finished["stats"] or ""):
            failures.append("run stats not stored")
        if seen != [("sub", run["id"])]:
            failures.append(f"handler args wrong: {seen}")
        logs = query_all("SELECT message FROM subscription_run_logs WHERE run_id=?", (run["id"],))
        if not any(row["message"] == "handler ran" for row in logs):
            failures.append("run log missing")

    subscription = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    if subscription["last_status"] != "success":
        failures.append(f"subscription last_status {subscription['last_status']}")
    if not subscription["next_run_at"] or subscription["next_run_at"] <= now():
        failures.append("next_run_at not advanced")

    # 手动触发 + 并发保护
    first = scheduler.trigger(subscription_id, trigger="manual")
    second = scheduler.trigger(subscription_id, trigger="manual")
    if first is None:
        failures.append("manual trigger returned no run")
    if second is not None:
        failures.append("concurrent manual trigger should be rejected")
    if first:
        manual = wait_run(first)
        if not manual or manual["status"] != "success":
            failures.append("manual run did not finish")
        if manual and manual["trigger"] != "manual":
            failures.append("manual trigger label missing")

    # 人工任务占用影视库时，调度器不领取
    execute(
        "UPDATE subscriptions SET next_run_at='2020-01-01T00:00:00+00:00', enabled=1 WHERE id=?",
        (subscription_id,),
    )
    task_id = execute(
        "INSERT INTO tasks(library_id,type,status,progress,total,done,stats,payload,"
        "cancel_requested,created_at) VALUES(1,'scan','queued',0,0,0,'{}','{}',0,?)",
        (now(),),
    )
    before = query_one(
        "SELECT COUNT(*) AS c FROM subscription_runs WHERE subscription_id=?", (subscription_id,)
    )["c"]
    scheduler._tick()
    after = query_one(
        "SELECT COUNT(*) AS c FROM subscription_runs WHERE subscription_id=?", (subscription_id,)
    )["c"]
    if after != before:
        failures.append("scheduler claimed while a task was queued")
    execute("UPDATE tasks SET status='success' WHERE id=?", (task_id,))

    # 没有注册执行器时不领取任务
    execute(
        "UPDATE subscriptions SET next_run_at='2020-01-01T00:00:00+00:00', enabled=1 WHERE id=?",
        (subscription_id,),
    )
    idle = SubscriptionScheduler(tick_seconds=1)
    idle._tick()
    pending = query_one(
        "SELECT COUNT(*) AS c FROM subscription_runs WHERE subscription_id=? AND id>?",
        (subscription_id, first or 0),
    )
    if pending and pending["c"]:
        failures.append("scheduler without handler claimed a run")

    # 重启恢复
    interrupted = create_run(subscription_id, trigger="schedule", status="running")
    recovered = recover_interrupted_runs()
    recovered_run = query_one("SELECT status FROM subscription_runs WHERE id=?", (interrupted,))
    if recovered < 1 or not recovered_run or recovered_run["status"] != "failed":
        failures.append("interrupted run not recovered")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("scheduler cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
