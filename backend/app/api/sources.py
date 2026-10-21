from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import dumps, execute, loads, now, query_all, query_one
from ..security import decrypt, encrypt
from ..core.presence_cache import clear_presence_cache
from ..core.providers import SourceError, build_source
from .deps import require_auth

router = APIRouter(prefix="/api/sources", tags=["sources"], dependencies=[Depends(require_auth)])

SECRET_KEYS = ("password", "token", "secret", "api_key")


class SourceBody(BaseModel):
    name: str
    type: str
    config: dict[str, Any]


class TestBody(BaseModel):
    type: str
    config: dict[str, Any]


class MkdirBody(BaseModel):
    path: str


def public_source(row: dict[str, Any]) -> dict[str, Any]:
    config = loads(row["config"], {}) or {}
    safe = {key: value for key, value in config.items() if key not in SECRET_KEYS}
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "config": safe,
        "has_password": any(bool(config.get(key)) for key in SECRET_KEYS),
        "created_at": row["created_at"],
    }


@router.get("")
def list_sources():
    rows = query_all("SELECT * FROM sources ORDER BY id DESC")
    result = []
    for row in rows:
        item = public_source(row)
        libs = query_all("SELECT id, name FROM libraries WHERE source_id=?", (row["id"],))
        item["libraries"] = libs
        result.append(item)
    return result


@router.post("")
def create_source(body: SourceBody):
    if body.type not in {"local", "webdav", "smb"}:
        raise HTTPException(status_code=400, detail="不支持的文件源类型")
    config = dict(body.config or {})
    for key in SECRET_KEYS:
        if config.get(key):
            config[key] = encrypt(str(config[key]))
    source_id = execute(
        "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
        (body.name.strip(), body.type, dumps(config), now(), now()),
    )
    return public_source(query_one("SELECT * FROM sources WHERE id=?", (source_id,)))


@router.put("/{source_id}")
def update_source(source_id: int, body: SourceBody):
    row = query_one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(status_code=404, detail="文件源不存在")
    existing = loads(row["config"], {}) or {}
    config = dict(body.config or {})
    for key in SECRET_KEYS:
        if config.get(key):
            config[key] = encrypt(str(config[key]))
        elif existing.get(key):
            config[key] = existing[key]
    execute(
        "UPDATE sources SET name=?, type=?, config=?, updated_at=? WHERE id=?",
        (body.name.strip(), body.type, dumps(config), now(), source_id),
    )
    clear_presence_cache()
    return public_source(query_one("SELECT * FROM sources WHERE id=?", (source_id,)))


@router.delete("/{source_id}")
def delete_source(source_id: int):
    row = query_one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(status_code=404, detail="文件源不存在")
    all_tasks = query_all("SELECT id FROM tasks WHERE library_id IN (SELECT id FROM libraries WHERE source_id=?)", (source_id,))
    for task in all_tasks:
        execute("DELETE FROM task_logs WHERE task_id=?", (task["id"],))
    execute("DELETE FROM tasks WHERE library_id IN (SELECT id FROM libraries WHERE source_id=?)", (source_id,))
    execute("DELETE FROM sources WHERE id=?", (source_id,))
    clear_presence_cache()
    return {"ok": True}


@router.post("/test")
def test_config(body: TestBody):
    try:
        source = build_source({"type": body.type, "config": body.config})
        result = source.test()
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{source_id}/test")
def test_source(source_id: int):
    row = query_one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(status_code=404, detail="文件源不存在")
    try:
        return build_source(row).test()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{source_id}/mkdir")
def make_dir(source_id: int, body: MkdirBody):
    row = query_one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(status_code=404, detail="文件源不存在")
    path = (body.path or "").strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="缺少文件夹路径")
    try:
        source = build_source(row)
        source.mkdir(path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "path": path}


@router.get("/{source_id}/browse")
def browse(source_id: int, path: str = ""):
    row = query_one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise HTTPException(status_code=404, detail="文件源不存在")
    try:
        source = build_source(row)
        entries = source.list_dirs(path)
        return {
            "path": path,
            "parent": path.rsplit("/", 1)[0] if path and "/" in path else "",
            "entries": [entry.to_dict() for entry in entries],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
