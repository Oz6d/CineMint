"""订阅运行记录的状态机与日志。"""

from __future__ import annotations

from typing import Any

from ..db import dumps, execute, execute_count, now, query_all, query_one

RUN_STATUSES = {"queued", "running", "success", "partial", "failed", "cancelled"}


def create_run(
    subscription_id: int,
    *,
    trigger: str = "schedule",
    status: str = "queued",
) -> int:
    return execute(
        "INSERT INTO subscription_runs(subscription_id, status, trigger, stats, created_at) "
        "VALUES(?,?,?,?,?)",
        (subscription_id, status, trigger, dumps({}), now()),
    )


def start_run(run_id: int) -> None:
    run = query_one("SELECT subscription_id FROM subscription_runs WHERE id=?", (run_id,))
    if not run:
        return
    timestamp = now()
    execute(
        "UPDATE subscription_runs SET status='running', started_at=? WHERE id=?",
        (timestamp, run_id),
    )
    execute(
        "UPDATE subscriptions SET last_run_at=?, last_status='running', updated_at=? WHERE id=?",
        (timestamp, timestamp, run["subscription_id"]),
    )


def update_run(run_id: int, **fields: Any) -> None:
    if not fields:
        return
    if "stats" in fields and not isinstance(fields["stats"], str):
        fields["stats"] = dumps(fields["stats"])
    keys = ", ".join(f"{key}=?" for key in fields)
    execute(f"UPDATE subscription_runs SET {keys} WHERE id=?", (*fields.values(), run_id))


def set_progress(
    run_id: int, done: int, total: int, *, message: str | None = None
) -> None:
    done = max(0, int(done))
    total = max(0, int(total))
    percent = round(min(100.0, done * 100.0 / total), 1) if total else 0.0
    fields: dict[str, Any] = {"done": done, "total": total, "progress": percent}
    if message is not None:
        fields["message"] = str(message)[:2000]
    update_run(run_id, **fields)


def log_run(run_id: int, message: str, level: str = "info") -> None:
    execute(
        "INSERT INTO subscription_run_logs(run_id, ts, level, message) VALUES(?,?,?,?)",
        (run_id, now(), level, str(message)[:2000]),
    )
    count = query_one(
        "SELECT COUNT(*) AS c FROM subscription_run_logs WHERE run_id=?", (run_id,)
    )
    if count and count["c"] > 800:
        execute(
            "DELETE FROM subscription_run_logs WHERE run_id=? AND id IN "
            "(SELECT id FROM subscription_run_logs WHERE run_id=? ORDER BY id ASC LIMIT 300)",
            (run_id, run_id),
        )


def finish_run(
    run_id: int,
    status: str,
    *,
    stats: dict[str, Any] | None = None,
    message: str = "",
    error: str | None = None,
) -> None:
    if status not in RUN_STATUSES:
        status = "failed"
    run = query_one("SELECT subscription_id FROM subscription_runs WHERE id=?", (run_id,))
    execute(
        "UPDATE subscription_runs SET status=?, stats=?, message=?, error=?, finished_at=? "
        "WHERE id=?",
        (
            status,
            dumps(stats or {}),
            str(message or "")[:2000],
            (str(error)[-4000:] if error else None),
            now(),
            run_id,
        ),
    )
    if not run:
        return
    execute(
        "UPDATE subscriptions SET last_status=?, last_message=?, updated_at=? WHERE id=?",
        (status, str(message or "")[:500], now(), run["subscription_id"]),
    )


def recover_interrupted_runs() -> int:
    affected = query_all(
        "SELECT DISTINCT subscription_id FROM subscription_runs "
        "WHERE status IN ('queued','running')"
    )
    count = execute_count(
        "UPDATE subscription_runs SET status='failed', error='服务重启导致任务中断', finished_at=? "
        "WHERE status IN ('queued','running')",
        (now(),),
    )
    for row in affected:
        execute(
            "UPDATE subscriptions SET last_status='failed', "
            "last_message='服务重启导致任务中断', updated_at=? WHERE id=?",
            (now(), row["subscription_id"]),
        )
    return count
