from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import dumps, execute, loads, now, query_all, query_one
from ..core.providers import norm
from ..core.cloud_paths import (
    cloud_root_parts,
    cloud_root_prefix,
    map_cloud_folder,
    staging_paths,
)
from ..core.quark import (
    QuarkError,
    QuarkShareError,
    parse_share_link,
)
from ..core.p115 import P115Error
from ..core.rss import (
    RssError,
    fetch_feed,
    is_candidate as rss_is_candidate,
    normalize_config as normalize_rss_config,
)
from ..core.rss_subscription import p115_client
from ..core.scheduler import scheduler
from ..core.scheduling import (
    CronError,
    CronExpression,
    next_run_for_subscription,
    normalize_schedule,
    parse_deadline,
)
from ..core.subscriptions import (
    SubscriptionConfigError,
    SubscriptionError,
    decode_config,
    encode_config,
    is_candidate,
    normalize_config,
    public_config,
    quark_client,
)
from .deps import require_auth

router = APIRouter(
    prefix="/api/subscriptions", tags=["subscriptions"], dependencies=[Depends(require_auth)]
)


class SubscriptionBody(BaseModel):
    name: str
    library_id: int
    type: str = "quark"
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    schedule: dict[str, Any] | str = Field(default_factory=dict)
    deadline: str = ""
    auto_organize: bool = False
    replace_versions: bool = True
    notify_channel_ids: list[int] = Field(default_factory=list)


class PreviewBody(BaseModel):
    name: str = "预览"
    type: str = "quark"
    config: dict[str, Any] = Field(default_factory=dict)
    page: int = 1
    page_size: int = 50


class FolderBody(BaseModel):
    library_id: int
    path: str = ""
    quark_root: str = ""


class P115FolderBody(BaseModel):
    library_id: int
    path: str = ""
    p115_root: str = ""


class CloudMkdirBody(BaseModel):
    library_id: int
    fid: str = ""
    path: str = ""
    name: str = ""
    cloud_root: str = ""


class ShareTreeBody(BaseModel):
    # 分享链接是必需内容；缺省时不走 Pydantic 422，而是和解析失败一样返回 400。
    share_url: str = ""
    passcode: str = ""
    path: str = ""
    fid: str = ""
    subscription_id: int | None = None


class ShareResolveBody(BaseModel):
    share_url: str = ""
    passcode: str = ""
    fid: str = ""
    subscription_id: int | None = None


_INVALID_FOLDER_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
SHARE_TREE_ENTRY_LIMIT = 1000


def _folder_name(value: str) -> str:
    name = (value or "").strip()
    if not name or name in {".", ".."} or _INVALID_FOLDER_NAME.search(name):
        raise HTTPException(status_code=400, detail="目录名称不能为空或包含 / \\ 等特殊字符")
    return name[:80]


def _cloud_path(value: str) -> str:
    path = norm(value)
    if path.startswith("..") or "/../" in f"/{path}/":
        raise HTTPException(status_code=400, detail="目录路径不能包含 ..")
    return path


def _saved_share_passcode(subscription_id: int | None, share_id: str) -> str:
    """编辑已有夸克订阅时回退保存的提取码；分享 ID 不同则不复用。"""
    if not subscription_id:
        return ""
    row = query_one(
        "SELECT type, config FROM subscriptions WHERE id=?", (int(subscription_id),)
    )
    if not row or str(row["type"]) != "quark":
        return ""
    try:
        config = decode_config(loads(row["config"], {}) or {})
        saved = parse_share_link(
            str(config.get("share_url") or ""), str(config.get("passcode") or "")
        )
    except QuarkShareError:
        return ""
    if not saved.passcode or saved.share_id != share_id:
        return ""
    return saved.passcode


def _tree_payload(
    library_root: str,
    cloud_root: str,
    fid: str,
    current: str,
    folders: list[dict[str, str]],
) -> dict[str, Any]:
    library_path = map_cloud_folder(library_root, cloud_root, current)
    entries = sorted(folders, key=lambda item: item["name"].lower())
    return {
        "fid": fid,
        "path": current,
        "cloud_root": cloud_root_prefix(library_root, cloud_root),
        "library_path": library_path,
        "inside_library": library_path is not None,
        "entries": [
            {
                "name": item["name"],
                "fid": item["fid"],
                "path": norm(f"{current}/{item['name']}") if current else norm(item["name"]),
            }
            for item in entries
        ],
    }


def run_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "subscription_id": row["subscription_id"],
        "status": row["status"],
        "trigger": row["trigger"],
        "progress": row["progress"],
        "total": row["total"],
        "done": row["done"],
        "stats": loads(row["stats"], {}) or {},
        "message": row["message"],
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def _normalize_for(sub_type: str, raw: dict[str, Any], name: str) -> dict[str, Any]:
    if sub_type == "rss":
        return normalize_rss_config(raw, name)
    return normalize_config(raw, name)


def _apply_cloud_folder(
    sub_type: str, config: dict[str, Any], library: dict[str, Any]
) -> dict[str, Any]:
    """把目录树选择的网盘目录映射为影视库内相对暂存路径。"""
    if sub_type == "rss":
        root_key, id_key, path_key = "p115_root", "p115_folder_id", "p115_folder_path"
    else:
        root_key, id_key, path_key = "quark_root", "quark_folder_id", "quark_folder_path"
    folder_id = str(config.get(id_key) or "").strip()
    if not folder_id:
        return config
    folder_path = norm(str(config.get(path_key) or ""))
    if not folder_path:
        raise HTTPException(status_code=400, detail="请选择有效的网盘目录")
    relative = map_cloud_folder(
        library["root_path"] or "", str(config.get(root_key) or ""), folder_path
    )
    if relative is None:
        raise HTTPException(
            status_code=400,
            detail="所选目录不在影视库范围内，请选择影视库根目录或其子目录",
        )
    result = dict(config)
    result["save_subdir"] = relative
    result["save_root"] = not relative
    return result


def public_subscription(row: dict[str, Any]) -> dict[str, Any]:
    config = decode_config(loads(row["config"], {}) or {})
    library = query_one("SELECT name FROM libraries WHERE id=?", (row["library_id"],))
    counts = {
        item["status"]: item["c"]
        for item in query_all(
            "SELECT status, COUNT(*) AS c FROM subscription_entries "
            "WHERE subscription_id=? GROUP BY status",
            (row["id"],),
        )
    }
    active = query_one(
        "SELECT * FROM subscription_runs WHERE subscription_id=? "
        "AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
        (row["id"],),
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "enabled": bool(row["enabled"]),
        "library_id": row["library_id"],
        "library_name": library["name"] if library else "已删除的影视库",
        "config": public_config(config, row["name"], row["type"]),
        "schedule": normalize_schedule(row["schedule"]),
        "next_run_at": row["next_run_at"],
        "deadline": row["deadline"] or "",
        "auto_organize": bool(row["auto_organize"]),
        "replace_versions": bool(row["replace_versions"]),
        "notify_channel_ids": loads(row["notify_channel_ids"], []) or [],
        "last_run_at": row["last_run_at"],
        "last_status": row["last_status"],
        "last_message": row["last_message"],
        "entry_counts": counts,
        "active_run": run_summary(active) if active else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _validate_schedule(value: dict[str, Any] | str) -> dict[str, Any]:
    try:
        normalized = normalize_schedule(value)
        if normalized["mode"] == "cron":
            CronExpression(normalized["cron"])
    except CronError as exc:
        raise HTTPException(status_code=400, detail=f"cron 表达式无效: {exc}") from exc
    return normalized


def _validate_deadline(value: str, schedule: dict[str, Any]) -> str:
    raw = (value or "").strip()
    if raw and parse_deadline(raw, schedule.get("timezone")) is None:
        raise HTTPException(status_code=400, detail="截止日期格式无效，请使用日期或 ISO 时间")
    return raw


@router.get("")
def list_subscriptions():
    rows = query_all("SELECT * FROM subscriptions ORDER BY id DESC")
    return [public_subscription(row) for row in rows]


@router.get("/runs")
def list_runs(limit: int = 50):
    rows = query_all(
        "SELECT * FROM subscription_runs ORDER BY id DESC LIMIT ?",
        (min(max(limit, 1), 200),),
    )
    return [run_summary(row) for row in rows]


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    row = query_one("SELECT * FROM subscription_runs WHERE id=?", (run_id,))
    if not row:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    result = run_summary(row)
    result["logs"] = query_all(
        "SELECT id, ts, level, message FROM subscription_run_logs "
        "WHERE run_id=? ORDER BY id DESC LIMIT 400",
        (run_id,),
    )[::-1]
    subscription = query_one(
        "SELECT id, name FROM subscriptions WHERE id=?", (row["subscription_id"],)
    )
    result["subscription_name"] = subscription["name"] if subscription else "已删除的订阅"
    return result


def _preview_page(raw: int, default: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


@router.post("/preview")
def preview(body: PreviewBody):
    if body.type not in {"quark", "rss"}:
        raise HTTPException(status_code=400, detail="不支持的订阅类型")
    try:
        config = _normalize_for(body.type, body.config, body.name or "预览")
    except (SubscriptionConfigError, RssError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from ..core.parser import parse_media_name

    requested_page = _preview_page(body.page, 1, 1_000_000)
    page_size = _preview_page(body.page_size, 50, 200)

    def paginate(items: list[dict[str, Any]], matched_count: int) -> dict[str, Any]:
        total_pages = max(1, (matched_count + page_size - 1) // page_size)
        page = min(requested_page, total_pages)
        start = (page - 1) * page_size
        has_more = start + page_size < matched_count
        return {
            "samples": items[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_more": has_more,
            "truncated": has_more,
        }

    if body.type == "rss":
        samples = []
        feeds = []
        total = 0
        matched_count = 0
        for feed_url in config["feed_urls"]:
            result = fetch_feed(feed_url)
            feeds.append(
                {
                    "url": feed_url,
                    "title": result.title,
                    "count": len(result.items),
                    "error": result.error,
                }
            )
            total += len(result.items)
            for item in result.items:
                if not rss_is_candidate(item, config):
                    continue
                matched_count += 1
                parsed = parse_media_name(item.title)
                samples.append(
                    {
                        "guid": item.guid,
                        "title": item.title,
                        "link": item.link,
                        "download_url": item.download_url,
                        "download_kind": item.download_kind,
                        "published_at": item.published_at,
                        "size": item.size,
                        "title_parsed": parsed.title,
                        "season": parsed.season,
                        "episode": parsed.episode,
                        "version": parsed.version,
                        "version_label": parsed.version_label,
                    }
                )
        page_data = paginate(samples, matched_count)
        return {
            "type": "rss",
            "total": total,
            "matched": matched_count,
            "feeds": feeds,
            **page_data,
        }

    try:
        client = quark_client()
        try:
            share = parse_share_link(config["share_url"], config["passcode"])
            stoken = client.share_token(share.share_id, share.passcode)
            files = client.walk_share(
                share.share_id,
                stoken,
                share_subdir=config["share_subdir"],
                share_fid=share.fid,
                include_subdirs=bool(config.get("include_subdirs", False)),
            )
        finally:
            client.close()
    except (QuarkError, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    matched = [file for file in files if is_candidate(file.name, config)]
    samples = []

    for file in matched:
        parsed = parse_media_name(file.name)
        samples.append(
            {
                "fid": file.fid,
                "name": file.name,
                "path": file.path,
                "size": file.size,
                "season": parsed.season,
                "episode": parsed.episode,
                "version": parsed.version,
                "version_label": parsed.version_label,
                "title": parsed.title,
            }
        )
    page_data = paginate(samples, len(matched))
    return {
        "type": "quark",
        "total": len(files),
        "matched": len(matched),
        **page_data,
    }


@router.get("/quark/folders")
def quark_folders(
    library_id: int,
    path: str = "",
    quark_root: str = "",
):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    relative = norm(path)
    if relative.startswith("..") or "/../" in f"/{relative}/":
        raise HTTPException(status_code=400, detail="目录路径不能包含 ..")
    _, quark_parts = staging_paths(
        library["root_path"] or "", relative, cloud_root=quark_root
    )
    try:
        client = quark_client()
        try:
            entries = client.list_subfolders(quark_parts)
        finally:
            client.close()
    except (QuarkError, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "path": relative,
        "quark_path": "/".join(quark_parts),
        "entries": [
            {"name": entry["name"], "path": norm(f"{relative}/{entry['name']}")}
            for entry in entries
        ],
    }


@router.post("/quark/folders")
def quark_mkdir(body: FolderBody):
    library = query_one("SELECT * FROM libraries WHERE id=?", (body.library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    relative = norm(body.path)
    if not relative or relative.startswith("..") or "/../" in f"/{relative}/":
        raise HTTPException(status_code=400, detail="请填写有效的目录路径")
    _, quark_parts = staging_paths(
        library["root_path"] or "", relative, cloud_root=body.quark_root
    )
    try:
        client = quark_client()
        try:
            client.ensure_folder_path(quark_parts)
        finally:
            client.close()
    except (QuarkError, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "path": relative}


@router.get("/p115/folders")
def p115_folders(
    library_id: int,
    path: str = "",
    p115_root: str = "",
):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    relative = norm(path)
    if relative.startswith("..") or "/../" in f"/{relative}/":
        raise HTTPException(status_code=400, detail="目录路径不能包含 ..")
    _, p115_parts = staging_paths(
        library["root_path"] or "", relative, cloud_root=p115_root
    )
    try:
        client = p115_client()
        try:
            entries = client.list_subfolders(p115_parts)
        finally:
            client.close()
    except (P115Error, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "path": relative,
        "p115_path": "/".join(p115_parts),
        "entries": [
            {"name": entry["name"], "path": norm(f"{relative}/{entry['name']}")}
            for entry in entries
        ],
    }


@router.post("/p115/folders")
def p115_mkdir(body: P115FolderBody):
    library = query_one("SELECT * FROM libraries WHERE id=?", (body.library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    relative = norm(body.path)
    if not relative or relative.startswith("..") or "/../" in f"/{relative}/":
        raise HTTPException(status_code=400, detail="请填写有效的目录路径")
    _, p115_parts = staging_paths(
        library["root_path"] or "", relative, cloud_root=body.p115_root
    )
    try:
        client = p115_client()
        try:
            client.ensure_folder_path(p115_parts)
        finally:
            client.close()
    except (P115Error, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "path": relative}


@router.get("/quark/tree")
def quark_tree(
    library_id: int,
    fid: str = "",
    path: str = "",
    cloud_root: str = "",
    from_root: bool = False,
):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    current = _cloud_path(path)
    current_fid = str(fid or "").strip()
    base_parts = cloud_root_parts(library["root_path"] or "", cloud_root)
    try:
        client = quark_client()
        try:
            if not current_fid:
                if from_root and not current:
                    current_fid = "0"
                else:
                    parts = current.split("/") if current else base_parts
                    current_fid = client.existing_folder_path(parts) if parts else "0"
                    current = "/".join(parts)
            folders = [
                {"name": entry.name, "fid": entry.fid}
                for entry in client.list_folder(current_fid)
                if entry.is_dir
            ]
        finally:
            client.close()
    except (QuarkError, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _tree_payload(
        library["root_path"] or "", cloud_root, current_fid, current, folders
    )


@router.post("/quark/tree")
def quark_tree_mkdir(body: CloudMkdirBody):
    library = query_one("SELECT * FROM libraries WHERE id=?", (body.library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    name = _folder_name(body.name)
    parent = _cloud_path(body.path)
    parent_fid = str(body.fid or "").strip()
    base_parts = cloud_root_parts(library["root_path"] or "", body.cloud_root)
    try:
        client = quark_client()
        try:
            if not parent_fid:
                parts = parent.split("/") if parent else base_parts
                parent_fid = client.existing_folder_path(parts) if parts else "0"
                parent = "/".join(parts)
            fid = client.create_folder(parent_fid, name)
        finally:
            client.close()
    except (QuarkError, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "name": name,
        "fid": fid,
        "path": norm(f"{parent}/{name}") if parent else norm(name),
    }


@router.post("/quark/share/tree")
def quark_share_tree(body: ShareTreeBody):
    """浏览夸克分享内的目录，返回当前目录下的文件夹与文件。"""
    path = _cloud_path(body.path)
    try:
        share = parse_share_link(body.share_url, body.passcode)
        if not share.passcode:
            share.passcode = _saved_share_passcode(
                body.subscription_id, share.share_id
            )
        client = quark_client()
        try:
            stoken = client.share_token(share.share_id, share.passcode)
            fid = str(body.fid or "").strip()
            if fid:
                # 优先按目录 ID 定位：分享链接里带 #/list/share/<fid> 时，
                # 选择器可以直接从当前链接所在目录打开，即使尚未解析 path。
                entry = client.find_share_fid(share.share_id, stoken, fid)
                if entry is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"分享中找不到该目录: {fid}",
                    )
            else:
                entry = client.find_share_dir(share.share_id, stoken, path)
                if entry is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"分享中找不到目录: {path or '（分享根目录）'}",
                    )
            children = client.list_share(share.share_id, stoken, entry.fid)
        finally:
            client.close()
    except (QuarkError, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    current = entry.path or ""
    entries = [
        {
            "fid": child.fid,
            "name": child.name,
            "is_dir": bool(child.is_dir),
            "size": int(child.size or 0),
            "path": norm(f"{current}/{child.name}") if current else norm(child.name),
        }
        for child in children
    ]
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))
    dirs = sum(1 for item in entries if item["is_dir"])
    return {
        "share_id": share.share_id,
        "fid": entry.fid,
        "path": current,
        "entries": entries[:SHARE_TREE_ENTRY_LIMIT],
        "dirs": dirs,
        "files": len(entries) - dirs,
        "truncated": len(entries) > SHARE_TREE_ENTRY_LIMIT,
    }


@router.post("/quark/share/resolve")
def quark_share_resolve(body: ShareResolveBody):
    """把分享链接里的 #/list/share/<fid> 解析成分享内目录路径。"""
    fid = str(body.fid or "").strip()
    try:
        share = parse_share_link(body.share_url, body.passcode)
        if not share.passcode:
            share.passcode = _saved_share_passcode(
                body.subscription_id, share.share_id
            )
        client = quark_client()
        try:
            stoken = client.share_token(share.share_id, share.passcode)
            entry = client.find_share_fid(share.share_id, stoken, fid)
            if entry is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"分享中找不到该目录: {fid}",
                )
        finally:
            client.close()
    except (QuarkError, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "share_id": share.share_id,
        "fid": entry.fid,
        "path": entry.path or "",
    }


@router.get("/p115/tree")
def p115_tree(
    library_id: int,
    fid: str = "",
    path: str = "",
    cloud_root: str = "",
    from_root: bool = False,
):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    current = _cloud_path(path)
    current_fid = str(fid or "").strip()
    base_parts = cloud_root_parts(library["root_path"] or "", cloud_root)
    try:
        client = p115_client()
        try:
            if not current_fid:
                if from_root and not current:
                    current_fid = "0"
                else:
                    parts = current.split("/") if current else base_parts
                    current_fid = client.resolve_folder(parts, create=False) if parts else "0"
                    current = "/".join(parts)
            folders = [
                {"name": entry["name"], "fid": entry["fid"]}
                for entry in client.list_entries(current_fid)
                if entry["is_dir"]
            ]
        finally:
            client.close()
    except (P115Error, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _tree_payload(
        library["root_path"] or "", cloud_root, current_fid, current, folders
    )


@router.post("/p115/tree")
def p115_tree_mkdir(body: CloudMkdirBody):
    library = query_one("SELECT * FROM libraries WHERE id=?", (body.library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    name = _folder_name(body.name)
    parent = _cloud_path(body.path)
    parent_fid = str(body.fid or "").strip()
    base_parts = cloud_root_parts(library["root_path"] or "", body.cloud_root)
    try:
        client = p115_client()
        try:
            if not parent_fid:
                parts = parent.split("/") if parent else base_parts
                parent_fid = client.resolve_folder(parts, create=False) if parts else "0"
                parent = "/".join(parts)
            fid = client.create_folder(parent_fid, name)
        finally:
            client.close()
    except (P115Error, SubscriptionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "name": name,
        "fid": fid,
        "path": norm(f"{parent}/{name}") if parent else norm(name),
    }


@router.post("")
def create_subscription(body: SubscriptionBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写订阅名称")
    if body.type not in {"quark", "rss"}:
        raise HTTPException(status_code=400, detail="不支持的订阅类型")
    library = query_one("SELECT * FROM libraries WHERE id=?", (body.library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    try:
        config = _normalize_for(body.type, body.config, name)
    except (SubscriptionConfigError, RssError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = _apply_cloud_folder(body.type, config, library)
    schedule = _validate_schedule(body.schedule)
    deadline = _validate_deadline(body.deadline, schedule)
    subscription_id = execute(
        "INSERT INTO subscriptions(name, type, enabled, library_id, config, schedule, "
        "deadline, auto_organize, replace_versions, notify_channel_ids, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            name[:80],
            body.type,
            int(bool(body.enabled)),
            body.library_id,
            dumps(encode_config(config)),
            dumps(schedule),
            deadline,
            int(bool(body.auto_organize)),
            int(bool(body.replace_versions)),
            dumps([int(value) for value in body.notify_channel_ids]),
            now(),
            now(),
        ),
    )
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    if row and row["enabled"]:
        # 新建并启用的订阅立即排队一次，由调度器在下一个 tick 领取，
        # 不必用户先手动点「运行」才转存已有内容。已过截止日期时仍不排队。
        scheduled = next_run_for_subscription(row)
        if scheduled:
            # 如果正常计划就在 5 分钟内，直接等计划触发，避免「新建立即跑一次
            # + 紧接着 cron 又跑一次」造成重复转存和重复推送。
            try:
                until_next = datetime.fromisoformat(scheduled) - datetime.fromisoformat(now())
            except (TypeError, ValueError):
                until_next = timedelta(minutes=6)
            if until_next > timedelta(minutes=5):
                scheduled = now()
            execute(
                "UPDATE subscriptions SET next_run_at=?, updated_at=? WHERE id=?",
                (scheduled, now(), subscription_id),
            )
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    return public_subscription(row)


@router.get("/{subscription_id}")
def get_subscription(subscription_id: int):
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    if not row:
        raise HTTPException(status_code=404, detail="订阅不存在")
    return public_subscription(row)


@router.get("/{subscription_id}/task-params")
def subscription_task_params(subscription_id: int):
    """导出任务参数：包含解密后的提取码，供「参数」复制 / 快速新建。

    与公开订阅列表不同，这里只给已登录管理员返回提取码；不额外暴露 Cookie / Key。
    """
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    if not row:
        raise HTTPException(status_code=404, detail="订阅不存在")
    result = public_subscription(row)
    config = decode_config(loads(row["config"], {}) or {})
    normalized = public_config(config, row["name"], row["type"])
    normalized["passcode"] = str(config.get("passcode") or "")
    result["config"] = normalized
    return result


@router.put("/{subscription_id}")
def update_subscription(subscription_id: int, body: SubscriptionBody):
    existing = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="订阅不存在")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写订阅名称")
    sub_type = str(existing["type"])
    if body.type != sub_type:
        raise HTTPException(status_code=400, detail="订阅类型不可修改")
    library = query_one("SELECT * FROM libraries WHERE id=?", (body.library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    old_config = decode_config(loads(existing["config"], {}) or {})
    raw_config = dict(body.config or {})
    if sub_type == "quark" and not raw_config.get("passcode") and old_config.get("passcode"):
        raw_config["passcode"] = old_config["passcode"]
    try:
        config = _normalize_for(sub_type, raw_config, name)
    except (SubscriptionConfigError, RssError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = _apply_cloud_folder(sub_type, config, library)
    schedule = _validate_schedule(body.schedule)
    deadline = _validate_deadline(body.deadline, schedule)
    execute(
        "UPDATE subscriptions SET name=?, enabled=?, library_id=?, config=?, schedule=?, "
        "deadline=?, auto_organize=?, replace_versions=?, notify_channel_ids=?, updated_at=? "
        "WHERE id=?",
        (
            name[:80],
            int(bool(body.enabled)),
            body.library_id,
            dumps(encode_config(config)),
            dumps(schedule),
            deadline,
            int(bool(body.auto_organize)),
            int(bool(body.replace_versions)),
            dumps([int(value) for value in body.notify_channel_ids]),
            now(),
            subscription_id,
        ),
    )
    if int(existing["library_id"]) != int(body.library_id):
        execute("DELETE FROM subscription_entries WHERE subscription_id=?", (subscription_id,))
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    next_at = next_run_for_subscription(row) if row["enabled"] else None
    execute(
        "UPDATE subscriptions SET next_run_at=?, updated_at=? WHERE id=?",
        (next_at, now(), subscription_id),
    )
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    return public_subscription(row)


@router.delete("/{subscription_id}")
def delete_subscription(subscription_id: int):
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    if not row:
        raise HTTPException(status_code=404, detail="订阅不存在")
    active = query_one(
        "SELECT id FROM subscription_runs WHERE subscription_id=? "
        "AND status IN ('queued','running') LIMIT 1",
        (subscription_id,),
    )
    if active:
        raise HTTPException(status_code=409, detail="订阅正在运行，请稍后再删除")
    execute("DELETE FROM subscriptions WHERE id=?", (subscription_id,))
    return {"ok": True}


@router.post("/{subscription_id}/run")
def trigger_subscription(subscription_id: int):
    try:
        run_id = scheduler.trigger(subscription_id, trigger="manual")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if run_id is None:
        raise HTTPException(
            status_code=409,
            detail="订阅正在运行，或影视库有扫描 / 整理任务占用",
        )
    return {"id": run_id}


@router.get("/{subscription_id}/runs")
def subscription_runs(subscription_id: int, limit: int = 30):
    if not query_one("SELECT id FROM subscriptions WHERE id=?", (subscription_id,)):
        raise HTTPException(status_code=404, detail="订阅不存在")
    rows = query_all(
        "SELECT * FROM subscription_runs WHERE subscription_id=? ORDER BY id DESC LIMIT ?",
        (subscription_id, min(max(limit, 1), 200)),
    )
    return [run_summary(row) for row in rows]


@router.get("/{subscription_id}/entries")
def subscription_entries(subscription_id: int, status: str = "", limit: int = 200):
    if not query_one("SELECT id FROM subscriptions WHERE id=?", (subscription_id,)):
        raise HTTPException(status_code=404, detail="订阅不存在")
    params: list[Any] = [subscription_id]
    sql = "SELECT * FROM subscription_entries WHERE subscription_id=?"
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(min(max(limit, 1), 1000))
    rows = query_all(sql, tuple(params))
    return [
        {
            "id": row["id"],
            "file_name": row["file_name"],
            "remote_path": row["remote_path"],
            "saved_path": row["saved_path"],
            "task_id": row["task_id"],
            "season": row["season"],
            "episode": row["episode"],
            "version": row["version"],
            "version_label": row["version_label"],
            "status": row["status"],
            "item_id": row["item_id"],
            "target_path": row["target_path"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
