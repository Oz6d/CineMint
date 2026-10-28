from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import dumps, execute, loads, now, query_all, query_one
from ..core.runner import runner
from .deps import require_auth

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_auth)])


def create_task(library_id: int, task_type: str, payload: dict[str, Any] | None = None) -> int:
    task_id = execute(
        "INSERT INTO tasks(library_id, type, status, payload, created_at) VALUES(?,?,?,?,?)",
        (library_id, task_type, "queued", dumps(payload or {}), now()),
    )
    runner.submit(task_id)
    return task_id


def task_summary(row: dict[str, Any]) -> dict[str, Any]:
    library = query_one("SELECT name FROM libraries WHERE id=?", (row["library_id"],)) if row["library_id"] else None
    return {
        "id": row["id"],
        "library_id": row["library_id"],
        "library_name": library["name"] if library else "已删除的影视库",
        "type": row["type"],
        "status": row["status"],
        "progress": row["progress"],
        "total": row["total"],
        "done": row["done"],
        "stats": loads(row["stats"], {}) or {},
        "payload": loads(row["payload"], {}) or {},
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


@router.get("")
def list_tasks(limit: int = 50):
    rows = query_all("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 200),))
    return [task_summary(row) for row in rows]


@router.get("/active")
def active_tasks():
    rows = query_all(
        "SELECT * FROM tasks WHERE status IN ('queued','running') ORDER BY id ASC"
    )
    return [task_summary(row) for row in rows]


@router.get("/{task_id}")
def get_task(task_id: int):
    row = query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = task_summary(row)
    logs = query_all(
        "SELECT id, ts, level, message FROM task_logs WHERE task_id=? ORDER BY id DESC LIMIT 400",
        (task_id,),
    )
    result["logs"] = list(reversed(logs))
    result["payload"] = loads(row["payload"], {}) or {}
    return result


class CancelBody(BaseModel):
    pass


@router.post("/{task_id}/cancel")
def cancel_task(task_id: int):
    row = query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    if row["status"] not in {"queued", "running"}:
        raise HTTPException(status_code=400, detail="任务已结束")
    execute("UPDATE tasks SET cancel_requested=1 WHERE id=?", (task_id,))
    return {"ok": True}


@router.post("/{task_id}/retry")
def retry_task(task_id: int):
    row = query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not row["library_id"]:
        raise HTTPException(status_code=400, detail="影视库已删除")
    new_id = create_task(row["library_id"], row["type"], loads(row["payload"], {}) or {})
    return {"id": new_id}
