from __future__ import annotations

import posixpath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import dumps, execute, loads, now, query_one
from ..core.attachment_links import (
    delete_attachment_link,
    load_attachment_links,
    upsert_attachment_link,
)
from ..core.attachments import (
    classify_attachment,
    is_attachment_directory,
    is_generic_asset,
    is_noise_image,
    load_directory_attachments,
)
from ..core.matcher import candidates_from_search_page
from ..core.metadata.tmdb import TMDBError, client_from_settings, image_url
from ..core.parser import VIDEO_EXTS, file_ext
from ..core.providers import SourceError, build_source, norm
from ..core.runner import runner
from .deps import require_auth
from .tasks import create_task

router = APIRouter(prefix="/api/items", tags=["items"], dependencies=[Depends(require_auth)])


class SearchBody(BaseModel):
    query: str
    media_type: str = ""
    year: int | None = None
    page: int = 1


class MatchBody(BaseModel):
    tmdb_id: int
    media_type: str
    season: int | None = None
    episode: int | None = None
    status: str = "matched"


class BatchMatchEntry(BaseModel):
    item_id: int
    season: int | None = None
    episode: int | None = None


class BatchMatchBody(BaseModel):
    tmdb_id: int
    media_type: str
    items: list[BatchMatchEntry]
    status: str = "matched"


class SearchAnyBody(BaseModel):
    query: str
    media_type: str = ""
    year: int | None = None
    page: int = 1


class PatchBody(BaseModel):
    season: int | None = None
    episode: int | None = None
    title: str | None = None
    year: int | None = None
    media_type: str | None = None
    status: str | None = None


class AttachmentLinkBody(BaseModel):
    path: str
    target_video_path: str | None = None


def decorate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        **candidate,
        "poster_url": image_url(candidate.get("poster_path") or "", "w500"),
        "backdrop_url": image_url(candidate.get("backdrop_path") or "", "w1280"),
        "logo_url": image_url(candidate.get("logo_path") or "", "w500"),
    }


def load_item(item_id: int) -> dict[str, Any]:
    row = query_one("SELECT * FROM items WHERE id=?", (item_id,))
    if not row:
        raise HTTPException(status_code=404, detail="文件不存在")
    return row


def item_payload(row: dict[str, Any]) -> dict[str, Any]:
    library = query_one("SELECT * FROM libraries WHERE id=?", (row["library_id"],))
    source = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],)) if library else None
    return {
        "id": row["id"],
        "library_id": row["library_id"],
        "library_name": library["name"] if library else "",
        "source_name": source["name"] if source else "",
        "file_name": row["file_name"],
        "source_path": row["source_path"],
        "size": row["size"],
        "status": row["status"],
        "error": row["error"],
        "parsed": loads(row["parsed"], {}) or {},
        "match": decorate(loads(row["match"], {}) or {}) if row["match"] else None,
        "candidates": [decorate(candidate) for candidate in (loads(row["candidates"], []) or [])],
        "manual": bool(row["manual"]),
        "target_path": row["target_path"],
        "updated_at": row["updated_at"],
    }


@router.get("/{item_id}")
def get_item(item_id: int):
    return item_payload(load_item(item_id))


def _empty_attachments(error: str = "") -> dict[str, Any]:
    return {
        "items": [],
        "folder_items": [],
        "unmatched": [],
        "counts": {"subtitle": 0, "audio": 0, "image": 0, "meta": 0},
        "total": 0,
        "error": error,
    }


def _view_paths(row: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    if row["status"] == "organized" and row["target_path"]:
        paths.append(norm(row["target_path"]))
    if row["source_path"] and norm(row["source_path"]) not in paths:
        paths.append(norm(row["source_path"]))
    return paths


def _existing_view_path(row: dict[str, Any], source) -> str:
    """优先使用目标路径；目标文件不在时回退源路径。"""
    paths = _view_paths(row)
    for path in paths:
        try:
            if source.exists(path):
                return path
        except SourceError:
            continue
    return paths[0] if paths else ""


def _attachment_payload(row: dict[str, Any]) -> dict[str, Any]:
    library = query_one("SELECT * FROM libraries WHERE id=?", (row["library_id"],))
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],)) if library else None
    if not source_row:
        return _empty_attachments("文件源不存在")
    parsed = loads(row["parsed"], {}) or {}
    try:
        source = build_source(source_row)
    except (SourceError, ValueError) as exc:
        return _empty_attachments(str(exc))
    manual_links = load_attachment_links(row["library_id"])
    error = ""
    for view_path in _view_paths(row):
        try:
            return load_directory_attachments(
                source,
                view_path,
                parsed,
                manual_links=manual_links,
            ).payload_for(view_path)
        except (SourceError, ValueError) as exc:
            error = str(exc)
    return _empty_attachments(error)


@router.get("/{item_id}/attachments")
def get_item_attachments(item_id: int):
    """读取视频所在目录，返回字幕 / 音轨 / 图片 / NFO 的匹配结果。

    已整理条目从目标路径读取，这样整理后也能看到实际跟着过去的文件。
    """
    return _attachment_payload(load_item(item_id))


@router.post("/{item_id}/attachments/link")
def link_item_attachment(item_id: int, body: AttachmentLinkBody):
    """手动把一个附带文件挂到当前视频。"""
    row = load_item(item_id)
    library = query_one("SELECT * FROM libraries WHERE id=?", (row["library_id"],))
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],)) if library else None
    if not source_row:
        raise HTTPException(status_code=400, detail="文件源不存在")
    attachment_path = norm(body.path)
    target_video = norm(body.target_video_path or "")
    if not attachment_path:
        raise HTTPException(status_code=400, detail="附带文件路径为空")
    name = posixpath.basename(attachment_path)
    kind = classify_attachment(name)
    if not kind:
        raise HTTPException(status_code=400, detail="这个文件不是可关联的字幕、音轨、图片或 NFO")
    if is_generic_asset(name):
        raise HTTPException(status_code=400, detail="文件夹级海报 / NFO 不需要手动关联，整理时会自动随文件夹移动")
    if kind == "image" and is_noise_image(name):
        raise HTTPException(status_code=400, detail="截图 / 二维码 / 频道图不会被当作影片附件")
    try:
        source = build_source(source_row)
        view_path = _existing_view_path(row, source)
        if not view_path:
            raise HTTPException(status_code=400, detail="找不到当前视频路径")
        video_path = target_video or view_path
        if target_video:
            if posixpath.dirname(target_video) != posixpath.dirname(norm(view_path)):
                raise HTTPException(status_code=400, detail="只能关联到同一目录下的视频")
            if file_ext(target_video) not in VIDEO_EXTS:
                raise HTTPException(status_code=400, detail="目标不是视频文件")
        if not is_attachment_directory(
            posixpath.dirname(norm(video_path)),
            posixpath.dirname(attachment_path),
        ):
            raise HTTPException(
                status_code=400,
                detail="只能关联当前视频同目录或 Subs / Sub / Subtitles 等字幕子目录下的文件",
            )
        if not source.exists(attachment_path):
            raise HTTPException(status_code=400, detail="附带文件已不存在，请刷新后重试")
        if not source.exists(video_path):
            raise HTTPException(status_code=400, detail="目标视频文件已不存在，请刷新后重试")
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    upsert_attachment_link(
        row["library_id"],
        video_path=video_path,
        attachment_path=attachment_path,
    )
    return _attachment_payload(row)


@router.post("/{item_id}/attachments/unlink")
def unlink_item_attachment(item_id: int, body: AttachmentLinkBody):
    """取消附带文件的手动关联，恢复自动匹配结果。"""
    row = load_item(item_id)
    attachment_path = norm(body.path)
    if not attachment_path:
        raise HTTPException(status_code=400, detail="附带文件路径为空")
    allowed_dirs = {posixpath.dirname(path) for path in _view_paths(row)}
    attachment_dir = posixpath.dirname(attachment_path)
    if not any(is_attachment_directory(video_dir, attachment_dir) for video_dir in allowed_dirs):
        raise HTTPException(
            status_code=400,
            detail="只能取消当前视频同目录或字幕子目录文件的手动关联",
        )
    delete_attachment_link(row["library_id"], attachment_path)
    return _attachment_payload(row)


@router.put("/{item_id}")
def patch_item(item_id: int, body: PatchBody):
    row = load_item(item_id)
    parsed = loads(row["parsed"], {}) or {}
    match = loads(row["match"], {}) or {}
    # 手动修改识别信息后同样标记为手动，避免下次扫描把修改覆盖掉。
    manual = bool(row["manual"])
    if body.season is not None:
        parsed["season"] = body.season or None
        manual = True
    if body.episode is not None:
        parsed["episode"] = body.episode or None
        manual = True
    if body.title:
        parsed["title"] = body.title
        manual = True
    if body.year is not None:
        parsed["year"] = body.year or None
        manual = True
    if body.media_type and match:
        match["media_type"] = body.media_type
        parsed["media_type"] = body.media_type
        manual = True
    status = body.status or row["status"]
    match_key = row["match_key"]
    if match and match.get("tmdb_id") and match.get("media_type"):
        match_key = f"{match['media_type']}:{match['tmdb_id']}"
    execute(
        "UPDATE items SET parsed=?, match=?, status=?, match_key=?, manual=?, updated_at=? WHERE id=?",
        (
            dumps(parsed),
            dumps(match) if match else None,
            status,
            match_key,
            1 if manual else 0,
            now(),
            item_id,
        ),
    )
    return item_payload(load_item(item_id))


@router.post("/search")
def search_any(body: SearchAnyBody):
    """批量纠错用：不依赖具体文件，直接搜索 TMDb。"""
    client = client_from_settings()
    if not client.configured:
        raise HTTPException(status_code=400, detail="请先在设置中填写 TMDb API Key")
    try:
        data = candidates_from_search_page(client, body.query, body.media_type, body.year, body.page)
    except TMDBError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "results": [decorate(candidate) for candidate in data["results"]],
        "page": data["page"],
        "total_pages": data["total_pages"],
        "total_results": data["total_results"],
    }


@router.get("/tmdb/{media_type}/{tmdb_id}")
def tmdb_lookup(media_type: str, tmdb_id: int):
    """批量纠错用：取作品详情（剧集含季列表）。"""
    if media_type not in {"movie", "tv"}:
        raise HTTPException(status_code=400, detail="媒体类型不合法")
    client = client_from_settings()
    if not client.configured:
        raise HTTPException(status_code=400, detail="请先在设置中填写 TMDb API Key")
    try:
        details = client.details(tmdb_id, media_type)
    except TMDBError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = decorate(details)
    payload["seasons"] = [
        {**season, "poster_url": image_url(season.get("poster_path") or "", "w342")}
        for season in payload.get("seasons", [])
    ]
    return payload


@router.get("/tmdb/tv/{tmdb_id}/season/{season_number}")
def tmdb_season(tmdb_id: int, season_number: int):
    """批量纠错用：取某季的集列表（含集标题），用于预览偏移结果。"""
    client = client_from_settings()
    if not client.configured:
        raise HTTPException(status_code=400, detail="请先在设置中填写 TMDb API Key")
    try:
        data = client.season(tmdb_id, season_number)
    except TMDBError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "season_number": data.get("season_number"),
        "episodes": [
            {**episode, "still_url": image_url(episode.get("still_path") or "", "w300")}
            for episode in data.get("episodes", [])
        ],
    }


@router.post("/batch-match")
def batch_match(body: BatchMatchBody):
    """把多个文件一次性改成同一部作品，并可整体指定季 / 集（偏移在界面侧计算）。"""
    if body.media_type not in {"movie", "tv"}:
        raise HTTPException(status_code=400, detail="媒体类型不合法")
    if not body.items:
        raise HTTPException(status_code=400, detail="请先选择要纠错的文件")
    client = client_from_settings()
    if not client.configured:
        raise HTTPException(status_code=400, detail="请先在设置中填写 TMDb API Key")
    try:
        details = client.details(body.tmdb_id, body.media_type)
    except TMDBError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    details["confidence"] = 1.0
    details["manual"] = True
    match_key = f"{body.media_type}:{body.tmdb_id}"
    updated = 0
    seen: set[int] = set()
    for entry in body.items:
        if entry.item_id in seen:
            continue
        seen.add(entry.item_id)
        row = query_one("SELECT * FROM items WHERE id=?", (entry.item_id,))
        if not row:
            continue
        parsed = loads(row["parsed"], {}) or {}
        if body.media_type == "tv":
            parsed["season"] = entry.season if entry.season is not None else (parsed.get("season") or 1)
            parsed["episode"] = entry.episode if entry.episode is not None else (parsed.get("episode") or 1)
        parsed["media_type"] = body.media_type
        execute(
            "UPDATE items SET match=?, parsed=?, status=?, error=NULL, manual=1, match_key=?, updated_at=? WHERE id=?",
            (dumps(details), dumps(parsed), body.status, match_key, now(), entry.item_id),
        )
        updated += 1
    return {"updated": updated}


@router.post("/{item_id}/search")
def search_item(item_id: int, body: SearchBody):
    row = load_item(item_id)
    client = client_from_settings()
    if not client.configured:
        raise HTTPException(status_code=400, detail="请先在设置中填写 TMDb API Key")
    try:
        data = candidates_from_search_page(client, body.query, body.media_type, body.year, body.page)
    except TMDBError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    results = data["results"]
    for candidate in results:
        if row["match"]:
            existing = loads(row["match"], {}) or {}
            candidate["selected"] = (
                existing.get("tmdb_id") == candidate["tmdb_id"]
                and existing.get("media_type") == candidate["media_type"]
            )
    return {
        "results": [decorate(candidate) for candidate in results],
        "page": data["page"],
        "total_pages": data["total_pages"],
        "total_results": data["total_results"],
    }


@router.post("/{item_id}/match")
def match_item(item_id: int, body: MatchBody):
    row = load_item(item_id)
    if body.media_type not in {"movie", "tv"}:
        raise HTTPException(status_code=400, detail="媒体类型不合法")
    client = client_from_settings()
    if not client.configured:
        raise HTTPException(status_code=400, detail="请先在设置中填写 TMDb API Key")
    try:
        details = client.details(body.tmdb_id, body.media_type)
    except TMDBError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    details["confidence"] = 1.0
    details["manual"] = True
    parsed = loads(row["parsed"], {}) or {}
    if body.season is not None:
        parsed["season"] = body.season
    if body.episode is not None:
        parsed["episode"] = body.episode
    if body.media_type == "tv" and parsed.get("season") is None:
        parsed["season"] = 1
    if body.media_type == "tv" and parsed.get("episode") is None:
        parsed["episode"] = 1
    parsed["media_type"] = body.media_type
    execute(
        "UPDATE items SET match=?, parsed=?, status=?, error=NULL, manual=1, match_key=?, updated_at=? WHERE id=?",
        (
            dumps(details),
            dumps(parsed),
            body.status,
            f"{body.media_type}:{body.tmdb_id}",
            now(),
            item_id,
        ),
    )
    return item_payload(load_item(item_id))


@router.post("/{item_id}/rematch")
def rematch_item(item_id: int):
    row = load_item(item_id)
    client = client_from_settings()
    if not client.configured:
        raise HTTPException(status_code=400, detail="请先在设置中填写 TMDb API Key")
    from ..core.matcher import match_item as run_match
    from ..core.parser import ParsedName

    parsed_dict = loads(row["parsed"], {}) or {}
    parsed = ParsedName(**{key: value for key, value in parsed_dict.items() if key in ParsedName.__dataclass_fields__})
    parsed.raw = parsed.raw or row["file_name"]
    result = run_match(parsed, None, client)
    best = result.get("best")
    # 「重新自动匹配」表示放弃手动识别结果，扫描时按自动匹配重新处理。
    execute(
        "UPDATE items SET parsed=?, match=?, candidates=?, status=?, error=?, match_key=?, manual=0, updated_at=? WHERE id=?",
        (
            dumps(parsed.to_dict()),
            dumps(best) if best else None,
            dumps(result.get("candidates") or []),
            result["status"],
            result.get("error"),
            f"{best['media_type']}:{best['tmdb_id']}" if best else None,
            now(),
            item_id,
        ),
    )
    return item_payload(load_item(item_id))


@router.post("/{item_id}/skip")
def skip_item(item_id: int):
    load_item(item_id)
    execute("UPDATE items SET status='ignored', updated_at=? WHERE id=?", (now(), item_id))
    return item_payload(load_item(item_id))


@router.post("/{item_id}/restore")
def restore_item(item_id: int):
    row = load_item(item_id)
    status = "matched" if row["match"] else "unmatched"
    execute("UPDATE items SET status=?, updated_at=? WHERE id=?", (status, now(), item_id))
    return item_payload(load_item(item_id))


@router.post("/{item_id}/organize")
def organize_item(item_id: int):
    row = load_item(item_id)
    if not row["match"]:
        raise HTTPException(status_code=400, detail="该文件还没有匹配到作品")
    task_id = create_task(row["library_id"], "organize", {"item_ids": [item_id]})
    return {"task_id": task_id}
