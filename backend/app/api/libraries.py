from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import dumps, execute, get_setting, loads, now, query_all, query_one
from ..core.attachment_links import delete_library_attachment_links, load_attachment_links
from ..core.classify import needs_details
from ..core.file_presence import filter_existing_rows
from ..core.presence_cache import invalidate_library
from ..core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE, build_target
from ..core.organize_targets import (
    delete_target,
    get_target,
    load_targets,
    relocate_organized_items,
    save_target,
    validate_target_directory,
    work_key,
)
from ..core.organizer import Organizer
from ..core.openlist import OpenListError, refresh_directory
from ..core.strm_sync import normalize_root as normalize_strm_root, validate_roots as validate_strm_roots
from ..core.parser import file_ext
from ..core.providers import build_source
from ..core.providers.base import SourceError, norm
from ..core.renamer import build_rename_plan
from ..core.metadata.tmdb import TMDBError, client_from_settings, image_url
from .deps import require_auth
from .tasks import create_task, task_summary

router = APIRouter(prefix="/api/libraries", tags=["libraries"], dependencies=[Depends(require_auth)])


class LibraryBody(BaseModel):
    name: str
    source_id: int
    root_path: str = ""
    target_root: str = ""
    media_type: str = "auto"
    naming: dict[str, Any] | None = None
    scrape: dict[str, Any] | None = None
    strm_sync_enabled: bool = False
    strm_source_root: str = ""
    strm_target_root: str = ""
    strm_delete_missing: bool = False


class OrganizeBody(BaseModel):
    item_ids: list[int] | None = None
    scope: str = "all"


class PlanBody(BaseModel):
    item_ids: list[int]


class WorkTargetBody(BaseModel):
    item_id: int | None = None
    match_key: str = ""
    path: str = ""
    mode: str = "category"
    move_existing: bool = False


class RenameBody(BaseModel):
    item_ids: list[int]
    rename_files: bool = True
    rename_folders: bool = True


def _active_subscription(library_id: int) -> dict[str, Any] | None:
    return query_one(
        "SELECT r.id, s.name FROM subscription_runs r "
        "JOIN subscriptions s ON s.id=r.subscription_id "
        "WHERE s.library_id=? AND r.status IN ('queued','running') LIMIT 1",
        (library_id,),
    )


def decorate_match(match: dict[str, Any] | None) -> dict[str, Any] | None:
    if not match:
        return None
    result = dict(match)
    result["poster_url"] = image_url(result.get("poster_path") or "", "w500")
    result["backdrop_url"] = image_url(result.get("backdrop_path") or "", "w1280")
    result["logo_url"] = image_url(result.get("logo_path") or "", "w500")
    return result


def serialize_item(row: dict[str, Any], library: dict[str, Any] | None = None) -> dict[str, Any]:
    parsed = loads(row["parsed"], {}) or {}
    match = loads(row["match"], {}) or {}
    auto_name = row["file_name"]
    if row["status"] == "organized" and row["target_path"]:
        auto_name = row["target_path"].rsplit("/", 1)[-1]
    elif match.get("tmdb_id"):
        library = library or query_one("SELECT * FROM libraries WHERE id=?", (row["library_id"],))
        if library:
            naming = {**DEFAULT_NAMING, **(loads(library["naming"], {}) or {})}
            try:
                _, auto_name = build_target(
                    parsed,
                    match,
                    naming,
                    extension=file_ext(row["source_path"]) or ".mkv",
                    source_name=row["file_name"],
                )
            except (KeyError, TypeError, ValueError):
                auto_name = row["file_name"]
    override = row["target_name_override"] if "target_name_override" in row.keys() else ""
    dir_override = row["target_dir_override"] if "target_dir_override" in row.keys() else ""
    return {
        "id": row["id"],
        "library_id": row["library_id"],
        "file_name": row["file_name"],
        "source_path": row["source_path"],
        "size": row["size"],
        "status": row["status"],
        "error": row["error"],
        "parsed": parsed,
        "match": decorate_match(loads(row["match"], {}) or None),
        "manual": bool(row["manual"]),
        "target_path": row["target_path"],
        "target_name_override": override or "",
        "target_dir_override": dir_override or "",
        "planned_name": override or auto_name,
        "auto_name": auto_name,
        "updated_at": row["updated_at"],
    }


def _mark_missing_rows(rows: list[dict[str, Any]]) -> int:
    """把已经确认不存在的源文件标记为 missing。

    只改状态、不删除记录：重新扫描看到文件时还能恢复；海报墙 / 作品计数
    会立即排除这些条目，不再留下「幽灵作品」。
    """
    changed = 0
    for row in rows:
        if row.get("status") == "missing":
            continue
        execute(
            "UPDATE items SET status='missing', updated_at=? WHERE id=?",
            (now(), row["id"]),
        )
        changed += 1
    return changed


def _restore_missing_rows(rows: list[dict[str, Any]]) -> int:
    """文件重新出现时，把之前标记为 missing 的条目恢复为已识别状态。"""
    restored = 0
    for row in rows:
        if row.get("status") != "missing":
            continue
        status = "matched" if (row.get("match") or row.get("match_key")) else "review"
        execute(
            "UPDATE items SET status=?, updated_at=? WHERE id=?",
            (status, now(), row["id"]),
        )
        row["status"] = status
        restored += 1
    return restored


def counts_for(library_id: int) -> dict[str, int]:
    rows = query_all(
        "SELECT status, COUNT(*) AS c FROM items WHERE library_id=? GROUP BY status",
        (library_id,),
    )
    counts = {row["status"]: row["c"] for row in rows}
    works = query_one(
        "SELECT COUNT(DISTINCT match_key) AS c FROM items WHERE library_id=? AND match_key IS NOT NULL "
        "AND status NOT IN ('organized','missing','ignored')",
        (library_id,),
    )
    return {
        "total": sum(counts.values()),
        "works": works["c"] if works else 0,
        "matched": counts.get("matched", 0),
        "review": counts.get("review", 0),
        "unmatched": counts.get("unmatched", 0),
        "organized": counts.get("organized", 0),
        "failed": counts.get("failed", 0),
        "ignored": counts.get("ignored", 0),
        "missing": counts.get("missing", 0),
        "pending": counts.get("matched", 0) + counts.get("review", 0) + counts.get("unmatched", 0),
    }


def serialize_library(row: dict[str, Any], with_counts: bool = True) -> dict[str, Any]:
    source = query_one("SELECT id, name, type FROM sources WHERE id=?", (row["source_id"],))
    result = {
        "id": row["id"],
        "name": row["name"],
        "source_id": row["source_id"],
        "source_name": source["name"] if source else "已删除",
        "source_type": source["type"] if source else "",
        "root_path": row["root_path"],
        "target_root": row["target_root"],
        "media_type": row["media_type"],
        "naming": loads(row["naming"], {}) or {},
        "scrape": loads(row["scrape"], {}) or {},
        "strm_sync_enabled": bool(row["strm_sync_enabled"]) if "strm_sync_enabled" in row.keys() else False,
        "strm_source_root": row["strm_source_root"] if "strm_source_root" in row.keys() else "",
        "strm_target_root": row["strm_target_root"] if "strm_target_root" in row.keys() else "",
        "strm_delete_missing": bool(row["strm_delete_missing"]) if "strm_delete_missing" in row.keys() else False,
        "enabled": bool(row["enabled"]),
        "is_demo": bool(row["is_demo"]),
        "last_scan_at": row["last_scan_at"],
        "created_at": row["created_at"],
    }
    if with_counts:
        result["counts"] = counts_for(row["id"])
        preview = query_all(
            "SELECT match FROM ("
            "  SELECT match, MAX(updated_at) AS ts FROM items "
            "  WHERE library_id=? AND match IS NOT NULL "
            "  AND status NOT IN ('ignored','organized','missing') "
            "  GROUP BY COALESCE(match_key, 'item:' || id)"
            ") AS recent ORDER BY ts DESC LIMIT 10",
            (row["id"],),
        )
        posters = []
        for item in preview:
            match = loads(item["match"], {}) or {}
            url = image_url(match.get("poster_path") or "", "w342")
            if url and url not in posters:
                posters.append(url)
            if len(posters) >= 5:
                break
        result["posters"] = posters
    return result


@router.get("")
def list_libraries():
    rows = query_all("SELECT * FROM libraries ORDER BY id DESC")
    return [serialize_library(row) for row in rows]


def _strm_settings(body: LibraryBody) -> tuple[int, str, str]:
    if not body.strm_sync_enabled:
        return (
            0,
            normalize_strm_root(body.strm_source_root),
            normalize_strm_root(body.strm_target_root),
        )
    try:
        source, target = validate_strm_roots(body.strm_source_root, body.strm_target_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return 1, source, target


@router.post("")
def create_library(body: LibraryBody):
    source = query_one("SELECT * FROM sources WHERE id=?", (body.source_id,))
    if not source:
        raise HTTPException(status_code=400, detail="文件源不存在")
    if body.media_type not in {"auto", "movie", "tv"}:
        raise HTTPException(status_code=400, detail="媒体类型不合法")
    strm_enabled, strm_source, strm_target = _strm_settings(body)
    library_id = execute(
        "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
        "strm_sync_enabled, strm_source_root, strm_target_root, strm_delete_missing, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            body.source_id,
            body.name.strip() or "未命名影视库",
            body.root_path.strip().strip("/"),
            body.target_root.strip().strip("/"),
            body.media_type,
            dumps({**DEFAULT_NAMING, **(body.naming or {})}),
            dumps({**DEFAULT_SCRAPE, **(body.scrape or {})}),
            strm_enabled,
            strm_source,
            strm_target,
            int(body.strm_delete_missing),
            now(),
            now(),
        ),
    )
    return serialize_library(query_one("SELECT * FROM libraries WHERE id=?", (library_id,)))


@router.get("/{library_id}")
def get_library(library_id: int):
    row = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not row:
        raise HTTPException(status_code=404, detail="影视库不存在")
    result = serialize_library(row)
    tasks = query_all(
        "SELECT * FROM tasks WHERE library_id=? ORDER BY id DESC LIMIT 8", (library_id,)
    )
    result["recent_tasks"] = [task_summary(task) for task in tasks]
    return result


@router.put("/{library_id}")
def update_library(library_id: int, body: LibraryBody):
    row = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not row:
        raise HTTPException(status_code=404, detail="影视库不存在")
    strm_enabled, strm_source, strm_target = _strm_settings(body)
    execute(
        "UPDATE libraries SET name=?, source_id=?, root_path=?, target_root=?, media_type=?, "
        "naming=?, scrape=?, strm_sync_enabled=?, strm_source_root=?, strm_target_root=?, "
        "strm_delete_missing=?, updated_at=? WHERE id=?",
        (
            body.name.strip() or row["name"],
            body.source_id,
            body.root_path.strip().strip("/"),
            body.target_root.strip().strip("/"),
            body.media_type,
            dumps({**DEFAULT_NAMING, **(body.naming or {})}),
            dumps({**DEFAULT_SCRAPE, **(body.scrape or {})}),
            strm_enabled,
            strm_source,
            strm_target,
            int(body.strm_delete_missing),
            now(),
            library_id,
        ),
    )
    invalidate_library(library_id)
    try:
        from ..core.ai_organize import invalidate_structure

        invalidate_structure(library_id)
    except Exception:
        pass
    return serialize_library(query_one("SELECT * FROM libraries WHERE id=?", (library_id,)))


@router.delete("/{library_id}")
def delete_library(library_id: int):
    row = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not row:
        raise HTTPException(status_code=404, detail="影视库不存在")
    task_rows = query_all("SELECT id FROM tasks WHERE library_id=?", (library_id,))
    for task in task_rows:
        execute("DELETE FROM task_logs WHERE task_id=?", (task["id"],))
    execute("DELETE FROM tasks WHERE library_id=?", (library_id,))
    execute("DELETE FROM library_auto_scan WHERE library_id=?", (library_id,))
    execute("DELETE FROM strm_sync_queue WHERE library_id=?", (library_id,))
    delete_library_attachment_links(library_id)
    execute("DELETE FROM libraries WHERE id=?", (library_id,))
    invalidate_library(library_id)
    try:
        from ..core.ai_organize import invalidate_structure

        invalidate_structure(library_id)
    except Exception:
        pass
    return {"ok": True}


@router.post("/{library_id}/scan")
def scan_library(library_id: int):
    row = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not row:
        raise HTTPException(status_code=404, detail="影视库不存在")
    if row["is_demo"] and not get_setting("tmdb_api_key", ""):
        raise HTTPException(
            status_code=400,
            detail="演示影视库需要先配置 TMDb API Key 才能重新扫描，否则会把演示识别结果重置为未匹配",
        )
    active = query_one(
        "SELECT id FROM tasks WHERE library_id=? AND type='scan' AND status IN ('queued','running')",
        (library_id,),
    )
    if active:
        raise HTTPException(status_code=409, detail="这个影视库正在扫描中")
    subscription = _active_subscription(library_id)
    if subscription:
        raise HTTPException(
            status_code=409, detail=f"订阅「{subscription['name']}」正在使用该影视库"
        )
    task_id = create_task(library_id, "scan", {})
    return {"task_id": task_id}


@router.post("/{library_id}/organize")
def organize_library(library_id: int, body: OrganizeBody):
    row = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not row:
        raise HTTPException(status_code=404, detail="影视库不存在")
    active = query_one(
        "SELECT id FROM tasks WHERE library_id=? AND status IN ('queued','running')",
        (library_id,),
    )
    if active:
        raise HTTPException(status_code=409, detail="该影视库已有任务在执行")
    subscription = _active_subscription(library_id)
    if subscription:
        raise HTTPException(
            status_code=409, detail=f"订阅「{subscription['name']}」正在使用该影视库"
        )
    payload: dict[str, Any] = {"item_ids": body.item_ids or [], "scope": body.scope}
    task_id = create_task(library_id, "organize", payload)
    return {"task_id": task_id}


@router.post("/{library_id}/rename-plan")
def preview_rename_plan(library_id: int, body: RenameBody):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    if not body.item_ids:
        raise HTTPException(status_code=400, detail="请选择要重命名的文件")
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    if not source_row:
        raise HTTPException(status_code=400, detail="文件源不存在")
    source = build_source(source_row)
    naming = {**DEFAULT_NAMING, **(loads(library["naming"], {}) or {})}
    root_path = library["root_path"] or ""

    placeholders = ",".join("?" for _ in body.item_ids)
    rows = query_all(
        f"SELECT * FROM items WHERE library_id=? AND id IN ({placeholders})",
        (library_id, *body.item_ids),
    )
    all_rows = query_all(
        "SELECT id, source_path, match_key FROM items WHERE library_id=?",
        (library_id,),
    )
    by_id = {int(row["id"]): row for row in rows}

    client = None
    season_cache: dict[tuple[int, int], dict[str, Any]] = {}

    def episode_title_for(match: dict[str, Any], parsed: dict[str, Any]) -> str:
        nonlocal client
        if not body.rename_files or not naming.get("episode_title", True):
            return ""
        if (match.get("media_type") or "movie") != "tv":
            return ""
        season_number, episode_number = parsed.get("season"), parsed.get("episode")
        if season_number is None or episode_number is None:
            return ""
        tmdb_id = int(match.get("tmdb_id") or 0)
        if not tmdb_id:
            return ""
        if client is None:
            client = client_from_settings()
        if not client.configured:
            return ""
        key = (tmdb_id, int(season_number))
        if key not in season_cache:
            try:
                season_cache[key] = client.season(*key)
            except (TMDBError, ValueError):
                season_cache[key] = {}
        for episode in season_cache[key].get("episodes", []):
            if int(episode.get("episode_number") or 0) == int(episode_number):
                return episode.get("name") or ""
        return ""

    def folder_guard_for(item: dict[str, Any]):
        match = loads(item.get("match"), {}) or {}
        match_key = norm(
            item.get("match_key")
            or f"{match.get('media_type') or 'movie'}:{match.get('tmdb_id') or ''}"
        )

        def guard(folder: str) -> bool:
            prefix = norm(folder)
            for other in all_rows:
                if int(other["id"]) == int(item["id"]):
                    continue
                path = norm(other.get("source_path") or "")
                if path == prefix or path.startswith(prefix + "/"):
                    if norm(other.get("match_key") or "") != match_key:
                        return False
            return True

        return guard

    plan_items = []
    for item_id in body.item_ids:
        item = by_id.get(int(item_id))
        if not item:
            continue
        match = loads(item["match"], {}) or {}
        parsed = loads(item["parsed"], {}) or {}
        plan = build_rename_plan(
            source=source,
            naming=naming,
            item=item,
            parsed=parsed,
            match=match,
            rename_files=body.rename_files,
            rename_folders=body.rename_folders,
            root_path=root_path,
            episode_title=episode_title_for(match, parsed),
            folder_guard=folder_guard_for(item),
            target_name=item.get("target_name_override") or "",
        )
        plan_items.append(plan.to_dict())
    return {"items": plan_items}


@router.post("/{library_id}/rename")
def rename_library(library_id: int, body: RenameBody):
    row = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not row:
        raise HTTPException(status_code=404, detail="影视库不存在")
    if not body.item_ids:
        raise HTTPException(status_code=400, detail="请选择要重命名的文件")
    if not body.rename_files and not body.rename_folders:
        raise HTTPException(status_code=400, detail="请至少选择重命名文件或重命名文件夹")
    active = query_one(
        "SELECT id FROM tasks WHERE library_id=? AND status IN ('queued','running')",
        (library_id,),
    )
    if active:
        raise HTTPException(status_code=409, detail="该影视库已有任务在执行")
    subscription = _active_subscription(library_id)
    if subscription:
        raise HTTPException(
            status_code=409, detail=f"订阅「{subscription['name']}」正在使用该影视库"
        )
    payload = {
        "item_ids": body.item_ids,
        "rename_files": body.rename_files,
        "rename_folders": body.rename_folders,
    }
    task_id = create_task(library_id, "rename", payload)
    return {"task_id": task_id}


@router.get("/{library_id}/items")
def library_items(
    library_id: int,
    status: str = "",
    q: str = "",
    limit: int = 200,
    offset: int = 0,
):
    row = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not row:
        raise HTTPException(status_code=404, detail="影视库不存在")
    clauses = ["library_id=?"]
    params: list[Any] = [library_id]
    if status and status != "all":
        statuses = [part for part in status.split(",") if part]
        if statuses:
            clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            params.extend(statuses)
    if q:
        clauses.append("(file_name LIKE ? OR source_path LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    where = " AND ".join(clauses)
    total = query_one(f"SELECT COUNT(*) AS c FROM items WHERE {where}", tuple(params))
    rows = query_all(
        f"SELECT * FROM items WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        (*params, min(max(limit, 1), 2000), max(offset, 0)),
    )
    items = [serialize_item(item, row) for item in rows]
    return {"total": total["c"] if total else 0, "items": items}


@router.get("/{library_id}/source-items")
def library_source_items(
    library_id: int,
    status: str = "",
    q: str = "",
    limit: int = 2000,
    offset: int = 0,
):
    """只返回待整理目录中真实存在、且尚未整理的条目。

    「全部文件」用它构建目录树，避免把已整理 / 源缺失 / 外部删除的记录
    当成当前文件显示。已整理记录仍在「已整理」标签中查看。
    """

    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    if not source_row:
        raise HTTPException(status_code=404, detail="文件源不存在")

    clauses = ["library_id=?", "status NOT IN ('organized','missing')"]
    params: list[Any] = [library_id]
    if status and status != "all":
        statuses = [part for part in status.split(",") if part]
        if statuses:
            clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            params.extend(statuses)
    if q:
        clauses.append("(file_name LIKE ? OR source_path LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    where = " AND ".join(clauses)
    rows = query_all(
        f"SELECT * FROM items WHERE {where} ORDER BY updated_at DESC",
        tuple(params),
    )

    try:
        source = build_source(source_row)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    present, hidden_rows, unverified_rows = filter_existing_rows(rows, source)
    _mark_missing_rows(hidden_rows)
    total = len(present)
    page = present[max(offset, 0) : max(offset, 0) + min(max(limit, 1), 2000)]
    return {
        "total": total,
        "items": [serialize_item(item, library) for item in page],
        "checked": len(rows),
        "hidden_missing": len(hidden_rows),
        "unverified": len(unverified_rows),
    }


@router.get("/{library_id}/works")
def library_works(library_id: int, q: str = "", refresh: bool = False):
    if refresh:
        invalidate_library(library_id)
    row = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not row:
        raise HTTPException(status_code=404, detail="影视库不存在")
    rows = query_all(
        "SELECT * FROM items WHERE library_id=? AND match_key IS NOT NULL "
        "AND status NOT IN ('ignored','organized') ORDER BY updated_at DESC",
        (library_id,),
    )
    if not rows:
        return {
            "works": [],
            "unmatched": counts_for(library_id)["unmatched"],
            "hidden_missing": 0,
            "unverified": 0,
            "error": "",
        }

    # 海报墙只反映待整理目录：已整理、源缺失、外部删除的文件都不再显示。
    source_row = query_one("SELECT * FROM sources WHERE id=?", (row["source_id"],))
    error = ""
    if not source_row:
        error = "文件源不存在"
        present: list[dict[str, Any]] = []
        hidden_rows: list[dict[str, Any]] = []
        unverified_rows: list[dict[str, Any]] = list(rows)
    else:
        try:
            present, hidden_rows, unverified_rows = filter_existing_rows(
                rows, build_source(source_row), library_id=library_id
            )
        except Exception as exc:
            error = str(exc)
            present = []
            hidden_rows = []
            unverified_rows = list(rows)
    # 目录里确认已经不存在 / 目录已被删除的条目直接标记 missing，
    # 不再让它们留在作品计数和海报墙里；文件重新出现时再恢复。
    _mark_missing_rows(hidden_rows)
    _restore_missing_rows(present)
    hidden_missing = len(hidden_rows)
    unverified = len(unverified_rows)

    groups: dict[str, list[dict[str, Any]]] = {}
    for item in present:
        groups.setdefault(item["match_key"], []).append(item)
    naming = {**DEFAULT_NAMING, **(loads(row["naming"], {}) or {})}
    works = []
    for key, group in groups.items():
        first_match = decorate_match(loads(group[0]["match"], {}) or None) or {}
        statuses: dict[str, int] = {}
        seasons: set[int] = set()
        episode_items = []
        for item in group:
            statuses[item["status"]] = statuses.get(item["status"], 0) + 1
            parsed = loads(item["parsed"], {}) or {}
            match = loads(item["match"], {}) or {}
            if parsed.get("season"):
                seasons.add(int(parsed["season"]))
            auto_name = item["file_name"]
            if item["status"] != "organized":
                try:
                    _, auto_name = build_target(
                        parsed,
                        match,
                        naming,
                        extension=file_ext(item["source_path"]) or ".mkv",
                        source_name=item["file_name"],
                    )
                except (KeyError, TypeError, ValueError):
                    auto_name = item["file_name"]
            planned_name = item.get("target_name_override") or auto_name
            episode_items.append(
                {
                    "id": item["id"],
                    "file_name": item["file_name"],
                    "planned_name": planned_name,
                    "auto_name": auto_name,
                    "target_name_override": item.get("target_name_override") or "",
                    "status": item["status"],
                    "season": parsed.get("season"),
                    "episode": parsed.get("episode"),
                    "poster_url": image_url(match.get("poster_path") or "", "w342"),
                    "target_path": item["target_path"],
                }
            )
        title = first_match.get("title") or ""
        if q and q.lower() not in title.lower() and q.lower() not in (first_match.get("original_title") or "").lower():
            continue
        works.append(
            {
                "key": key,
                "tmdb_id": first_match.get("tmdb_id"),
                "media_type": first_match.get("media_type"),
                "title": title,
                "original_title": first_match.get("original_title"),
                "year": first_match.get("year"),
                "overview": first_match.get("overview"),
                "poster_url": first_match.get("poster_url"),
                "backdrop_url": first_match.get("backdrop_url"),
                "logo_url": first_match.get("logo_url"),
                "vote_average": first_match.get("vote_average"),
                "genres": first_match.get("genres") or [],
                "counts": {
                    "total": len(group),
                    **{status: count for status, count in statuses.items()},
                },
                "seasons": sorted(seasons),
                "items": sorted(
                    episode_items,
                    key=lambda entry: (
                        int(entry.get("season") or 0),
                        int(entry.get("episode") or 0),
                    ),
                ),
            }
        )
    works.sort(key=lambda work: (work.get("year") or 0, work.get("title") or ""), reverse=True)
    return {
        "works": works,
        "unmatched": counts_for(library_id)["unmatched"],
        "hidden_missing": hidden_missing,
        "unverified": unverified,
        "error": error,
    }


def _resolve_work_key(library_id: int, item_id: int | None, match_key: str) -> str:
    if item_id:
        row = query_one(
            "SELECT * FROM items WHERE library_id=? AND id=?",
            (library_id, item_id),
        )
        if not row:
            raise HTTPException(status_code=404, detail="文件不存在")
        key = row["match_key"] or work_key(loads(row["match"], {}) or {})
    else:
        key = (match_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="该文件还没有匹配到作品")
    return key


def _ensure_work_idle(library_id: int) -> None:
    active = query_one(
        "SELECT id FROM tasks WHERE library_id=? AND status IN ('queued','running')",
        (library_id,),
    )
    if active or _active_subscription(library_id):
        raise HTTPException(
            status_code=409,
            detail="该影视库有任务正在运行，请稍后再调整整理位置",
        )


def _refresh_dir_quietly(source_row: dict[str, Any] | None, path: str) -> bool:
    """迁移后清理空目录前刷新网盘目录缓存；失败不影响迁移结果。"""
    if not source_row or not path:
        return False
    try:
        return bool(refresh_directory(source_row, path))
    except OpenListError:
        return False


@router.get("/{library_id}/work-target")
def get_work_target(
    library_id: int,
    item_id: int | None = None,
    match_key: str = "",
):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    key = _resolve_work_key(library_id, item_id, match_key)
    return {
        "match_key": key,
        "target_root": library["target_root"] or "",
        "target": get_target(library_id, key),
    }


@router.put("/{library_id}/work-target")
def save_work_target(library_id: int, body: WorkTargetBody):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    _ensure_work_idle(library_id)
    key = _resolve_work_key(library_id, body.item_id, body.match_key)
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    if not source_row:
        raise HTTPException(status_code=400, detail="影视库的文件源不存在")
    source = build_source(source_row)
    try:
        path = validate_target_directory(
            source,
            library["target_root"] or "",
            body.path,
            body.mode,
        )
        target = save_target(library_id, key, path, body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    move: dict[str, Any] | None = None
    move_error = ""
    if body.move_existing:
        try:
            move = relocate_organized_items(
                library,
                source,
                {**target, "match_key": key},
                refresh_dir=lambda value: _refresh_dir_quietly(source_row, value),
            )
        except (SourceError, OSError, ValueError) as exc:
            move_error = str(exc)
    invalidate_library(library_id)
    return {"target": target, "move": move, "move_error": move_error}


@router.delete("/{library_id}/work-target")
def restore_work_target(
    library_id: int,
    item_id: int | None = None,
    match_key: str = "",
):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    _ensure_work_idle(library_id)
    key = _resolve_work_key(library_id, item_id, match_key)
    restored = delete_target(library_id, key)
    invalidate_library(library_id)
    return {"match_key": key, "restored": restored}


@router.post("/{library_id}/plan")
def preview_plan(library_id: int, body: PlanBody):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    if not body.item_ids:
        raise HTTPException(status_code=400, detail="请选择要整理的文件")
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    source = build_source(source_row)
    targets = load_targets(library_id)
    organizer = Organizer(
        source,
        {**DEFAULT_NAMING, **(loads(library["naming"], {}) or {})},
        {**DEFAULT_SCRAPE, **(loads(library["scrape"], {}) or {})},
        manual_links=load_attachment_links(library_id),
        targets=targets,
    )
    placeholders = ",".join("?" for _ in body.item_ids)
    rows = query_all(
        f"SELECT * FROM items WHERE library_id=? AND id IN ({placeholders})",
        (library_id, *body.item_ids),
    )

    client = None
    details_cache: dict[tuple[str, int], dict[str, Any]] = {}

    def details_for(match: dict[str, Any]) -> dict[str, Any]:
        nonlocal client
        key = (match.get("media_type") or "movie", int(match.get("tmdb_id") or 0))
        if key not in details_cache:
            if client is None:
                client = client_from_settings()
            try:
                details_cache[key] = client.details(key[1], key[0]) if client.configured else {}
            except (TMDBError, ValueError):
                details_cache[key] = {}
        return details_cache[key]

    auto_category = bool(organizer.naming.get("auto_category", True))
    ai_mode = str(get_setting("ai_organize_mode", "suggest") or "suggest")
    ai_structure = None
    ai_seen: set[str] = set()
    ai_preview_left = 8
    plan = []
    for item in rows:
        match = loads(item["match"], {}) or {}
        parsed = loads(item["parsed"], {}) or {}
        if not match.get("tmdb_id"):
            plan.append({"id": item["id"], "file_name": item["file_name"], "error": "未匹配"})
            continue
        key = item["match_key"] or work_key(match)
        pinned = targets.get(key) if key else None
        details = (
            details_for(match)
            if auto_category and not pinned and needs_details(match)
            else {}
        )
        item_target = item.get("target_dir_override") or ""
        ai_target = None
        if (
            ai_mode != "off"
            and not pinned
            and not item_target
            and key
            and key not in ai_seen
            and ai_preview_left > 0
        ):
            ai_seen.add(key)
            ai_preview_left -= 1
            try:
                from ..core.ai_organize import build_structure, suggest_category

                if ai_structure is None:
                    ai_structure = build_structure(
                        library_id, source, library["target_root"] or ""
                    )
                ai_target = suggest_category(
                    library,
                    source,
                    match,
                    details_for(match),
                    structure=ai_structure,
                )
            except Exception:
                ai_target = None
        folder, target = organizer.target_for(
            parsed,
            match,
            source_path=item["source_path"],
            target_root=library["target_root"] or "",
            details=details,
            target_name=item.get("target_name_override") or "",
            target_dir=item_target,
        )
        _, auto_target = organizer.target_for(
            parsed,
            match,
            source_path=item["source_path"],
            target_root=library["target_root"] or "",
            details=details,
        )
        attachments = organizer.attachments_for(item["source_path"], parsed)
        plan.append(
            {
                "id": item["id"],
                "file_name": item["file_name"],
                "source_path": item["source_path"],
                "folder": folder,
                "target_path": target,
                "target_name_override": item.get("target_name_override") or "",
                "auto_name": auto_target.rsplit("/", 1)[-1],
                "media_type": match.get("media_type"),
                "title": match.get("title"),
                "season": parsed.get("season"),
                "episode": parsed.get("episode"),
                "organize_target": pinned,
                "ai_target": ai_target,
                "item_target": {"path": item_target} if item_target else None,
                "attachments": [*attachments["items"], *attachments["folder_items"]],
                "attachment_counts": attachments["counts"],
                "attachment_total": attachments["total"],
                "attachment_unmatched": len(attachments["unmatched"]),
            }
        )
    return {"items": plan}


@router.get("/{library_id}/target-structure")
def target_structure(library_id: int):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    if not source_row:
        raise HTTPException(status_code=400, detail="文件源不存在")
    source = build_source(source_row)
    organizer = Organizer(source, {**DEFAULT_NAMING, **(loads(library["naming"], {}) or {})}, {})
    return {
        "target_root": library["target_root"] or "",
        "tree": organizer.detect_structure(library["target_root"] or ""),
    }
