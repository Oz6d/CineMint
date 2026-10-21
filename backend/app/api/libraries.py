from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import dumps, execute, get_setting, loads, now, query_all, query_one
from ..core.attachment_links import delete_library_attachment_links, load_attachment_links
from ..core.classify import needs_details
from ..core.file_presence import filter_existing
from ..core.presence_cache import invalidate_library
from ..core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE, build_target
from ..core.organizer import Organizer
from ..core.parser import file_ext
from ..core.providers import build_source
from ..core.providers.base import norm
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


class OrganizeBody(BaseModel):
    item_ids: list[int] | None = None
    scope: str = "all"


class PlanBody(BaseModel):
    item_ids: list[int]


class RenameBody(BaseModel):
    item_ids: list[int]
    rename_files: bool = True
    rename_folders: bool = True


def decorate_match(match: dict[str, Any] | None) -> dict[str, Any] | None:
    if not match:
        return None
    result = dict(match)
    result["poster_url"] = image_url(result.get("poster_path") or "", "w500")
    result["backdrop_url"] = image_url(result.get("backdrop_path") or "", "w1280")
    result["logo_url"] = image_url(result.get("logo_path") or "", "w500")
    return result


def serialize_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "library_id": row["library_id"],
        "file_name": row["file_name"],
        "source_path": row["source_path"],
        "size": row["size"],
        "status": row["status"],
        "error": row["error"],
        "parsed": loads(row["parsed"], {}) or {},
        "match": decorate_match(loads(row["match"], {}) or None),
        "manual": bool(row["manual"]),
        "target_path": row["target_path"],
        "updated_at": row["updated_at"],
    }


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


@router.post("")
def create_library(body: LibraryBody):
    source = query_one("SELECT * FROM sources WHERE id=?", (body.source_id,))
    if not source:
        raise HTTPException(status_code=400, detail="文件源不存在")
    if body.media_type not in {"auto", "movie", "tv"}:
        raise HTTPException(status_code=400, detail="媒体类型不合法")
    library_id = execute(
        "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
        "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            body.source_id,
            body.name.strip() or "未命名影视库",
            body.root_path.strip().strip("/"),
            body.target_root.strip().strip("/"),
            body.media_type,
            dumps({**DEFAULT_NAMING, **(body.naming or {})}),
            dumps({**DEFAULT_SCRAPE, **(body.scrape or {})}),
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
    execute(
        "UPDATE libraries SET name=?, source_id=?, root_path=?, target_root=?, media_type=?, "
        "naming=?, scrape=?, updated_at=? WHERE id=?",
        (
            body.name.strip() or row["name"],
            body.source_id,
            body.root_path.strip().strip("/"),
            body.target_root.strip().strip("/"),
            body.media_type,
            dumps({**DEFAULT_NAMING, **(body.naming or {})}),
            dumps({**DEFAULT_SCRAPE, **(body.scrape or {})}),
            now(),
            library_id,
        ),
    )
    invalidate_library(library_id)
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
    delete_library_attachment_links(library_id)
    execute("DELETE FROM libraries WHERE id=?", (library_id,))
    invalidate_library(library_id)
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
    items = [serialize_item(item) for item in rows]
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

    present, hidden_missing, unverified = filter_existing(rows, source)
    total = len(present)
    page = present[max(offset, 0) : max(offset, 0) + min(max(limit, 1), 2000)]
    return {
        "total": total,
        "items": [serialize_item(item) for item in page],
        "checked": len(rows),
        "hidden_missing": hidden_missing,
        "unverified": unverified,
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
        "AND status NOT IN ('ignored','organized','missing') ORDER BY updated_at DESC",
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
    hidden_missing = 0
    unverified = 0
    error = ""
    if not source_row:
        error = "文件源不存在"
        unverified = len(rows)
        present: list[dict[str, Any]] = []
    else:
        try:
            present, hidden_missing, unverified = filter_existing(
                rows, build_source(source_row), library_id=library_id
            )
        except Exception as exc:
            error = str(exc)
            unverified = len(rows)
            present = []

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
            planned_name = item["file_name"]
            if item["status"] != "organized":
                try:
                    _, planned_name = build_target(
                        parsed,
                        match,
                        naming,
                        extension=file_ext(item["source_path"]) or ".mkv",
                        source_name=item["file_name"],
                    )
                except (KeyError, TypeError, ValueError):
                    planned_name = item["file_name"]
            episode_items.append(
                {
                    "id": item["id"],
                    "file_name": item["file_name"],
                    "planned_name": planned_name,
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


@router.post("/{library_id}/plan")
def preview_plan(library_id: int, body: PlanBody):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    if not body.item_ids:
        raise HTTPException(status_code=400, detail="请选择要整理的文件")
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    source = build_source(source_row)
    organizer = Organizer(
        source,
        {**DEFAULT_NAMING, **(loads(library["naming"], {}) or {})},
        {**DEFAULT_SCRAPE, **(loads(library["scrape"], {}) or {})},
        manual_links=load_attachment_links(library_id),
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
    plan = []
    for item in rows:
        match = loads(item["match"], {}) or {}
        parsed = loads(item["parsed"], {}) or {}
        if not match.get("tmdb_id"):
            plan.append({"id": item["id"], "file_name": item["file_name"], "error": "未匹配"})
            continue
        details = details_for(match) if auto_category and needs_details(match) else {}
        folder, target = organizer.target_for(
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
                "media_type": match.get("media_type"),
                "title": match.get("title"),
                "season": parsed.get("season"),
                "episode": parsed.get("episode"),
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
