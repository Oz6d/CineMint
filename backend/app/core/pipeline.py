"""扫描 / 整理的共享管线。

任务中心与订阅调度都走这里，避免两套实现逐渐分叉。
调用方通过 hooks 接收日志、进度与取消信号。
"""

from __future__ import annotations

import posixpath
import re
import time
from collections import defaultdict
from typing import Any, Protocol

from ..db import dumps, execute, get_setting, loads, now, query_all, query_one
from .attachment_links import load_attachment_links
from .matcher import match_from_alias, match_item
from .metadata.tmdb import TMDBError, client_from_settings
from .naming import DEFAULT_NAMING, DEFAULT_SCRAPE
from .openlist import OpenListError, refresh_directory
from .organizer import Organizer
from .parser import VIDEO_EXTS, file_ext, parse_media_name
from .providers import SourceError, SourceNotFound, build_source, parent_of
from .scraper import ImageFetcher

SKIP_DIRS = {
    "@eadir", "#recycle", "$recycle.bin", "system volume information",
    ".trash", ".@__thumb", "lost+found", "node_modules", ".git",
    "filmix.trash", "recycle bin", "recycler",
}
GENERIC_SEASON_RE = re.compile(
    r"^(?:season[ ._-]*\d+|s\d{1,2}|第[一二三四五六七八九十百零两\d]+季|"
    r"(?:cd|disc)[ ._-]*\d+)$",
    re.I,
)


class PipelineCancelled(Exception):
    pass


class PipelineHooks(Protocol):
    def log(self, message: str, level: str = "info") -> None: ...

    def update(self, **fields: Any) -> None: ...

    def cancelled(self) -> bool: ...


class NullHooks:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def log(self, message: str, level: str = "info") -> None:
        self.messages.append((level, message))

    def update(self, **fields: Any) -> None:
        return

    def cancelled(self) -> bool:
        return False


def _basename(path: str) -> str:
    return posixpath.basename((path or "").rstrip("/"))


def _skip_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered in SKIP_DIRS or lowered.endswith(".trash") or name.startswith(".")


def _path_under(path: str, prefix: str) -> bool:
    from .providers import norm

    path, prefix = norm(path), norm(prefix)
    return bool(prefix) and (path == prefix or path.startswith(prefix + "/"))


def _replace_prefix(path: str, old_prefix: str, new_prefix: str) -> str:
    from .providers import norm

    path, old_prefix, new_prefix = norm(path), norm(old_prefix), norm(new_prefix)
    if path == old_prefix:
        return new_prefix
    if path.startswith(old_prefix + "/"):
        return new_prefix + path[len(old_prefix):]
    return path


def _remap_item_sources(library_id: int, old_prefix: str, new_prefix: str) -> None:
    """文件夹改名后同步数据库里所有受影响的源路径（包括未选中的同目录条目）。"""
    rows = query_all("SELECT id, source_path FROM items WHERE library_id=?", (library_id,))
    for row in rows:
        current = _normalize(row.get("source_path") or "")
        if not _path_under(current, old_prefix):
            continue
        updated = _replace_prefix(current, old_prefix, new_prefix)
        if updated != current:
            execute(
                "UPDATE items SET source_path=?, updated_at=? WHERE id=?",
                (updated, now(), row["id"]),
            )


def _remap_attachment_paths(library_id: int, mapper) -> None:
    rows = query_all(
        "SELECT id, video_path, attachment_path, created_at FROM attachment_links WHERE library_id=?",
        (library_id,),
    )
    for row in rows:
        video_path = _normalize(row.get("video_path") or "")
        attachment_path = _normalize(row.get("attachment_path") or "")
        new_video = mapper(video_path)
        new_attachment = mapper(attachment_path)
        if new_video == video_path and new_attachment == attachment_path:
            continue
        execute("DELETE FROM attachment_links WHERE id=?", (row["id"],))
        execute(
            """
            INSERT INTO attachment_links(library_id, video_path, attachment_path, created_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(library_id, attachment_path) DO UPDATE SET
                video_path=excluded.video_path,
                created_at=excluded.created_at
            """,
            (library_id, new_video, new_attachment, row.get("created_at") or now()),
        )


def _normalize(path: str) -> str:
    from .providers import norm

    return norm(path)


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_fields(parsed: Any) -> tuple[int | None, int | None, int, str]:
    return (
        _int_or_none(getattr(parsed, "season", None)),
        _int_or_none(getattr(parsed, "episode", None)),
        int(getattr(parsed, "version", 0) or 0),
        str(getattr(parsed, "version_label", "") or "")[:32],
    )


def _folder_context(source_path: str):
    parent = parent_of(source_path)
    if not parent:
        return None
    parsed = parse_media_name(_basename(parent), is_dir=True)
    raw = _basename(parent)
    if GENERIC_SEASON_RE.match(raw) or not parsed.title:
        grand = parent_of(parent)
        if grand:
            return parse_media_name(_basename(grand), is_dir=True)
    return parsed


def _merge_context(parsed, folder):
    if not folder:
        return parsed
    if not parsed.title or len(parsed.title) < 2:
        parsed.title = folder.title
    if not parsed.title_alt:
        parsed.title_alt = folder.title_alt
    if not parsed.year:
        parsed.year = folder.year
    if parsed.season is None:
        parsed.season = folder.season
    if not parsed.media_type and folder.media_type:
        parsed.media_type = folder.media_type
    if parsed.episode is not None:
        parsed.media_type = "tv"
    return parsed


def _mark_stale(
    hooks: PipelineHooks,
    library_id: int,
    seen_paths: set[str],
    failed_dirs: list[str],
    scope: str,
) -> int:
    """把本次扫描未出现、且所在目录没有临时读取失败的旧条目标记为丢失。"""
    stale = query_all(
        "SELECT id, source_path FROM items WHERE library_id=? "
        "AND status IN ('matched','review','unmatched','failed')",
        (library_id,),
    )
    # 扫描根目录临时不可读时无法判断文件是否存在，本次不做清理。
    if "" in failed_dirs or (scope and scope in failed_dirs):
        return 0
    missing = 0
    for row in stale:
        path = row["source_path"]
        if scope and not _path_under(path, scope):
            continue
        if path in seen_paths:
            continue
        if any(_path_under(path, directory) for directory in failed_dirs):
            continue
        execute(
            "UPDATE items SET status='missing', updated_at=? WHERE id=?",
            (now(), row["id"]),
        )
        missing += 1
    if missing:
        hooks.log(f"{missing} 个旧条目在源目录中已不存在，标记为丢失", "warn")
    return missing


def _refresh_cached_dir(
    source_row: dict[str, Any], path: str, hooks: PipelineHooks
) -> None:
    """扫描前让 OpenList 回源刷新目录缓存，避免扫描到旧目录。"""
    if not path:
        return
    try:
        if refresh_directory(source_row, path):
            hooks.log(f"已刷新 OpenList 目录缓存：{path}")
    except OpenListError as exc:
        hooks.log(f"OpenList 刷新目录失败：{exc}", "warn")


def scan_library(
    library_id: int,
    hooks: PipelineHooks | None = None,
    *,
    root_path: str | None = None,
    scope: str | None = None,
    mark_missing: bool = True,
    subscription_id: int | None = None,
    refresh_cache: bool = False,
) -> dict[str, Any]:
    """扫描影视库；可限定到某个子目录，供订阅暂存区增量扫描。"""
    hooks = hooks or NullHooks()
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise RuntimeError("影视库不存在")
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    if not source_row:
        raise RuntimeError("文件源不存在")
    source = build_source(source_row)
    scan_root = root_path if root_path is not None else (library["root_path"] or "")
    stale_scope = scan_root if scope is None else scope
    library_name = library["name"]
    if refresh_cache:
        _refresh_cached_dir(source_row, scan_root, hooks)
    hooks.log(f"开始扫描「{library_name}」" + (f"（{scan_root}）" if root_path is not None else ""))

    min_size_mb = float(get_setting("min_size_mb", 10) or 0)
    max_files = int(get_setting("max_files_per_scan", 3000) or 3000)
    allow_iso = bool(get_setting("allow_iso", True))

    files: list[Any] = []
    queue_dirs: list[tuple[str, int]] = [(scan_root, 0)]
    visited = 0
    dirs_read = 0
    missing_dirs = 0
    failed_dirs: list[str] = []
    seen_paths: set[str] = set()
    last_beat = time.monotonic()
    while queue_dirs:
        if hooks.cancelled():
            raise PipelineCancelled()
        current, depth = queue_dirs.pop(0)
        if depth > 8:
            continue
        dirs_read += 1
        try:
            entries = source.list(current)
        except SourceNotFound as exc:
            missing_dirs += 1
            hooks.log(f"目录已不存在 {current or '/'}: {exc}", "warn")
            entries = []
        except SourceError as exc:
            missing_dirs += 1
            failed_dirs.append(current)
            hooks.log(f"跳过目录 {current or '/'}: {exc}", "warn")
            entries = []
        for entry in entries:
            visited += 1
            if entry.is_dir:
                if _skip_dir(entry.name):
                    continue
                queue_dirs.append((entry.path, depth + 1))
                continue
            ext = file_ext(entry.name)
            if ext not in VIDEO_EXTS:
                continue
            if ext == ".iso" and not allow_iso:
                continue
            if entry.size and entry.size < min_size_mb * 1024 * 1024:
                continue
            files.append(entry)
            if len(files) >= max_files:
                hooks.log(f"文件数达到上限 {max_files}，其余文件本次不处理", "warn")
                queue_dirs.clear()
                break

        tick = time.monotonic()
        if tick - last_beat >= 10:
            last_beat = tick
            hooks.update(stats=dumps({"visited": visited, "dirs": dirs_read, "found": len(files)}))
            hooks.log(
                f"正在读取目录：已检查 {visited} 个条目，发现 {len(files)} 个视频"
                f"（已读取 {dirs_read} 个目录）",
            )

    if missing_dirs:
        hooks.log(
            f"本次跳过 {missing_dirs} 个无法访问的目录（常见于网盘回收站或已删除目录）",
            "warn",
        )
    total = len(files)
    hooks.update(
        total=total,
        progress=2,
        stats=dumps({"found": total, "visited": visited, "dirs": dirs_read}),
    )
    hooks.log(f"扫描到 {total} 个视频文件（共检查 {visited} 个条目）")

    if not total:
        missing = (
            _mark_stale(hooks, library_id, seen_paths, failed_dirs, stale_scope)
            if mark_missing
            else 0
        )
        result = {
            "stats": {
                "found": 0,
                "matched": 0,
                "review": 0,
                "unmatched": 0,
                "missing": missing,
                "visited": visited,
                "dirs": dirs_read,
            },
            "total": 0,
            "items": [],
            "item_ids": [],
        }
        hooks.update(
            status="success",
            progress=100,
            finished_at=now(),
            total=0,
            done=0,
            stats=dumps(result["stats"]),
        )
        return result

    api_key = get_setting("tmdb_api_key", "") or ""
    client = client_from_settings()
    if not api_key:
        hooks.log("未配置 TMDb API Key，只做文件名解析，不做在线匹配", "warn")
    auto_threshold = float(get_setting("auto_threshold", 0.72) or 0.72)
    review_threshold = float(get_setting("review_threshold", 0.5) or 0.5)

    stats: defaultdict[str, int] = defaultdict(int)
    processed: list[dict[str, Any]] = []
    for index, entry in enumerate(files, start=1):
        if hooks.cancelled():
            raise PipelineCancelled()
        source_path = entry.path
        seen_paths.add(source_path)
        parsed = parse_media_name(entry.name)
        folder_parsed = _folder_context(source_path)
        parsed = _merge_context(parsed, folder_parsed)
        if not parsed.title and folder_parsed and folder_parsed.title:
            parsed.title = folder_parsed.title
        if not parsed.year and folder_parsed and folder_parsed.year:
            parsed.year = folder_parsed.year

        existing = query_one(
            "SELECT * FROM items WHERE library_id=? AND source_path=?",
            (library_id, source_path),
        )
        if existing and existing["status"] == "organized":
            stats["skipped"] += 1
            continue

        if existing and existing["status"] == "ignored":
            # 用户手动忽略或订阅判重（同集保留更高版本）的结论不能被重扫复活。
            stats["skipped"] += 1
            continue

        match_data: dict[str, Any] = {}
        status = "unmatched"
        error = ""
        if existing and existing.get("manual"):
            # 手动识别结果在下方统一保留，这里不再重复走自动匹配，也不重复计数。
            status = existing["status"]
        elif parsed.is_extra:
            status = "ignored"
            error = "疑似预告/花絮/样片"
            stats["ignored"] += 1
            hooks.log(f"忽略样本/花絮: {entry.name}", "debug")
        else:
            alias_result = match_from_alias(parsed)
            if alias_result:
                best = alias_result.get("best") or {}
                # 手动匹配过的剧集，后续集通常同样没有季号，按第 1 季延续。
                if (
                    best.get("media_type") == "tv"
                    and parsed.episode is not None
                    and parsed.season is None
                ):
                    parsed.season = 1
                if best.get("media_type"):
                    parsed.media_type = str(best["media_type"])
                status = "matched"
                match_data = alias_result
                stats["matched"] += 1
                hooks.log(
                    f"[别名匹配] {entry.name} → {best.get('title', '?')} "
                    f"(S{parsed.season if parsed.season is not None else '?'}"
                    f"E{parsed.episode})",
                    "debug",
                )
            elif not api_key:
                error = "未配置 TMDb API Key"
                stats["unmatched"] += 1
            else:
                result = match_item(
                    parsed,
                    folder_parsed,
                    client,
                    auto_threshold=auto_threshold,
                    review_threshold=review_threshold,
                )
                status = result["status"]
                match_data = result
                error = result.get("error") or ""
                stats[status] += 1
                if status in {"matched", "review"}:
                    best = result.get("best") or {}
                    hooks.log(
                        f"[{status}] {entry.name} → {best.get('title', '?')} "
                        f"({best.get('year') or '?'}) {int((result.get('confidence') or 0) * 100)}%",
                        "debug",
                    )
                elif index % 10 == 0:
                    hooks.log(f"已处理 {index}/{total}", "debug")

        best = match_data.get("best") if match_data else None
        match_key = f"{best.get('media_type')}:{best.get('tmdb_id')}" if best else None
        candidates = match_data.get("candidates") if match_data else []
        match_payload = {**best, "confidence": match_data.get("confidence")} if best else None
        season, episode, version, version_label = _parse_fields(parsed)

        if existing and existing.get("manual"):
            # 手动匹配写入的识别信息（媒体类型 / 季集号）不能被重新扫描覆盖。
            stored = loads(existing["parsed"], {}) or {}
            merged = parsed.to_dict()
            for key in ("media_type", "season", "episode"):
                value = stored.get(key)
                if value not in (None, ""):
                    merged[key] = value
            season = _int_or_none(merged.get("season"))
            episode = _int_or_none(merged.get("episode"))
            restored_status = existing["status"]
            if restored_status == "missing":
                restored_status = "matched" if existing.get("match") else "review"
            execute(
                "UPDATE items SET size=?, mtime=?, parsed=?, file_name=?, status=?, "
                "season=?, episode=?, version=?, version_label=?, "
                "subscription_id=COALESCE(subscription_id, ?), updated_at=? WHERE id=?",
                (
                    entry.size,
                    entry.mtime,
                    dumps(merged),
                    entry.name,
                    restored_status,
                    season,
                    episode,
                    version,
                    version_label,
                    subscription_id,
                    now(),
                    existing["id"],
                ),
            )
            stats["manual_kept"] += 1
            processed.append(
                {
                    "id": existing["id"],
                    "source_path": source_path,
                    "file_name": entry.name,
                    "status": restored_status,
                }
            )
            continue

        values = (
            entry.size,
            entry.mtime,
            dumps(parsed.to_dict()),
            dumps(match_payload) if match_payload else None,
            dumps(candidates or []),
            status,
            error or None,
            match_key,
            season,
            episode,
            version,
            version_label,
        )
        if existing:
            execute(
                "UPDATE items SET size=?, mtime=?, parsed=?, match=?, candidates=?, status=?, "
                "error=?, match_key=?, season=?, episode=?, version=?, version_label=?, "
                "subscription_id=COALESCE(subscription_id, ?), updated_at=? WHERE id=?",
                (*values, subscription_id, now(), existing["id"]),
            )
            item_id = existing["id"]
        else:
            item_id = execute(
                "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, "
                "match, candidates, status, error, match_key, season, episode, version, "
                "version_label, subscription_id, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    library_id,
                    source_path,
                    entry.name,
                    *values,
                    subscription_id,
                    now(),
                    now(),
                ),
            )
        processed.append(
            {
                "id": item_id,
                "source_path": source_path,
                "file_name": entry.name,
                "status": status,
            }
        )

        progress = 2 + (index / total) * 96
        hooks.update(progress=round(progress, 1), done=index, stats=dumps(dict(stats)))

    missing = (
        _mark_stale(hooks, library_id, seen_paths, failed_dirs, stale_scope)
        if mark_missing
        else 0
    )

    execute(
        "UPDATE libraries SET last_scan_at=?, updated_at=? WHERE id=?",
        (now(), now(), library_id),
    )
    stats["missing"] = missing
    stats["visited"] = visited
    stats["dirs"] = dirs_read
    result_stats = dict(stats)
    hooks.update(
        status="success",
        progress=100,
        finished_at=now(),
        stats=dumps(result_stats),
        total=total,
        done=len(files),
    )
    hooks.log(
        f"扫描完成：匹配 {stats['matched']}，待确认 {stats['review']}，"
        f"未识别 {stats['unmatched']}，忽略 {stats['ignored']}",
    )
    return {
        "stats": result_stats,
        "total": total,
        "items": processed,
        "item_ids": [item["id"] for item in processed],
    }


def organize_items(
    library_id: int,
    hooks: PipelineHooks | None = None,
    *,
    item_ids: list[int] | None = None,
    statuses: tuple[str, ...] = ("matched", "review"),
    collision: str = "",
) -> dict[str, Any]:
    hooks = hooks or NullHooks()
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise RuntimeError("影视库不存在")
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    if not source_row:
        raise RuntimeError("文件源不存在")
    source = build_source(source_row)

    ids = [int(item) for item in (item_ids or [])]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        items = query_all(
            f"SELECT * FROM items WHERE library_id=? AND id IN ({placeholders})",
            (library_id, *ids),
        )
    else:
        placeholders = ",".join("?" for _ in statuses)
        items = query_all(
            f"SELECT * FROM items WHERE library_id=? AND status IN ({placeholders}) "
            "AND match IS NOT NULL",
            (library_id, *statuses),
        )
    if not items:
        hooks.update(
            status="success",
            progress=100,
            finished_at=now(),
            stats=dumps({"success": 0}),
            total=0,
            done=0,
        )
        hooks.log("没有可整理的条目")
        return {"status": "success", "stats": {"success": 0}, "total": 0, "results": []}

    total = len(items)
    hooks.update(total=total, progress=1)
    hooks.log(f"开始整理 {total} 个文件")

    client = client_from_settings()
    details_cache: dict[tuple[str, int], dict[str, Any]] = {}
    season_cache: dict[tuple[int, int], dict[str, Any]] = {}
    images = ImageFetcher()
    organizer = Organizer(
        source,
        naming={**DEFAULT_NAMING, **(loads(library["naming"], {}) or {})},
        scrape={**DEFAULT_SCRAPE, **(loads(library["scrape"], {}) or {})},
        images=images,
        manual_links=load_attachment_links(library["id"]),
        source_root=library["root_path"] or "",
    )
    target_root = library["target_root"] or ""
    scraped_folders: set[str] = set()
    stats: defaultdict[str, int] = defaultdict(int)
    warnings: list[str] = []
    results: list[dict[str, Any]] = []

    def details_for(match: dict[str, Any]) -> dict[str, Any]:
        key = (match.get("media_type") or "movie", int(match.get("tmdb_id") or 0))
        if key not in details_cache:
            try:
                details_cache[key] = client.details(key[1], key[0])
            except (TMDBError, ValueError):
                details_cache[key] = match
        return details_cache[key]

    def season_for(tv_id: int, season_number: int) -> dict[str, Any]:
        key = (tv_id, season_number)
        if key not in season_cache:
            try:
                season_cache[key] = client.season(tv_id, season_number)
            except TMDBError:
                season_cache[key] = {"episodes": []}
        return season_cache[key]

    for index, item in enumerate(items, start=1):
        if hooks.cancelled():
            raise PipelineCancelled()
        match = loads(item["match"], {}) or {}
        parsed = loads(item["parsed"], {}) or {}
        if not match or not match.get("tmdb_id"):
            stats["skipped"] += 1
            results.append(
                {
                    "item_id": item["id"],
                    "file_name": item["file_name"],
                    "status": "skipped",
                    "error": "没有匹配",
                }
            )
            hooks.log(f"跳过（没有匹配）: {item['file_name']}", "warn")
            continue
        media_type = match.get("media_type") or "movie"
        season_number = parsed.get("season")
        episode_number = parsed.get("episode")
        if media_type == "tv" and (season_number is None or episode_number is None):
            stats["failed"] += 1
            message = "剧集缺少季/集号，请先修正识别信息"
            execute(
                "UPDATE items SET status='failed', error=?, updated_at=? WHERE id=?",
                (message, now(), item["id"]),
            )
            results.append(
                {
                    "item_id": item["id"],
                    "file_name": item["file_name"],
                    "status": "failed",
                    "error": message,
                }
            )
            hooks.log(f"失败: {item['file_name']} - {message}", "error")
            continue

        details = details_for(match)
        episode: dict[str, Any] = {}
        episode_title = ""
        if media_type == "tv":
            season = season_for(int(match["tmdb_id"]), int(season_number))
            for ep in season.get("episodes", []):
                if int(ep.get("episode_number") or 0) == int(episode_number):
                    episode = ep
                    episode_title = ep.get("name") or ""
                    break

        show_folder = organizer.show_folder_for(match, target_root, details=details)
        if show_folder not in scraped_folders:
            try:
                seasons_present: set[int] = set()
                for row in items:
                    row_match = loads(row["match"], {}) or {}
                    if row_match.get("tmdb_id") != match.get("tmdb_id"):
                        continue
                    row_parsed = loads(row["parsed"], {}) or {}
                    if row_parsed.get("season"):
                        seasons_present.add(int(row_parsed["season"]))
                season_details = (
                    {
                        number: season_for(int(match["tmdb_id"]), number)
                        for number in sorted(seasons_present)
                    }
                    if media_type == "tv"
                    else {}
                )
                organizer.scrape_show(show_folder, match, details, season_details)
            except SourceError as exc:
                warnings.append(f"刮削目录失败 {show_folder}: {exc}")
            scraped_folders.add(show_folder)

        try:
            result = organizer.organize(
                source_path=item["source_path"],
                parsed=parsed,
                match=match,
                target_root=target_root,
                episode_title=episode_title,
                season_number=int(season_number) if season_number else None,
                episode_number=int(episode_number) if episode_number else None,
                episode=episode,
                details=details,
                target_name=item.get("target_name_override") or "",
                collision=collision,
            )
            if result.skipped:
                stats["skipped"] += 1
                execute(
                    "UPDATE items SET error=?, updated_at=? WHERE id=?",
                    (result.skip_reason[:800], now(), item["id"]),
                )
                results.append(
                    {
                        "item_id": item["id"],
                        "file_name": item["file_name"],
                        "status": "skipped",
                        "error": result.skip_reason,
                    }
                )
                hooks.log(f"跳过 [{index}/{total}]: {item['file_name']} - {result.skip_reason}", "warn")
                progress = 1 + (index / total) * 99
                hooks.update(progress=round(progress, 1), done=index, stats=dumps(dict(stats)))
                continue
            stats["success"] += 1
            warnings.extend(result.warnings or [])
            execute(
                "UPDATE items SET status='organized', target_path=?, error=NULL, updated_at=? WHERE id=?",
                (result.target_path, now(), item["id"]),
            )
            if result.moved_paths:
                _remap_attachment_paths(
                    library_id,
                    lambda path, mapping=result.moved_paths: mapping.get(_normalize(path), _normalize(path)),
                )
            attachment_note = ""
            if result.sidecars or result.folder_assets:
                attachment_note = (
                    f"（附带 {result.sidecars} 个文件"
                    + (f" + {result.folder_assets} 个文件夹级资源" if result.folder_assets else "")
                    + "）"
                )
            cleanup_note = (
                f"（清理空目录 {len(result.removed_dirs)} 个）"
                if result.removed_dirs
                else ""
            )
            hooks.log(
                f"完成 [{index}/{total}]: {item['file_name']} → {result.target_path}"
                f"{attachment_note}{cleanup_note}",
            )
            results.append(
                {
                    "item_id": item["id"],
                    "file_name": item["file_name"],
                    "status": "organized",
                    "target_path": result.target_path,
                    "warnings": list(result.warnings or []),
                }
            )
        except (SourceError, OSError) as exc:
            stats["failed"] += 1
            message = str(exc)[:800]
            execute(
                "UPDATE items SET status='failed', error=?, updated_at=? WHERE id=?",
                (message, now(), item["id"]),
            )
            results.append(
                {
                    "item_id": item["id"],
                    "file_name": item["file_name"],
                    "status": "failed",
                    "error": message,
                }
            )
            hooks.log(f"失败 [{index}/{total}]: {item['file_name']} - {message}", "error")

        progress = 1 + (index / total) * 99
        hooks.update(progress=round(progress, 1), done=index, stats=dumps(dict(stats)))

    for warning in warnings[:20]:
        hooks.log(warning, "warn")
    status = "success"
    if stats["failed"]:
        status = "partial" if stats["success"] else "failed"
    hooks.update(
        status=status,
        progress=100,
        finished_at=now(),
        stats=dumps(dict(stats)),
        total=total,
        done=total,
    )
    hooks.log(
        f"整理完成：成功 {stats['success']}，失败 {stats['failed']}，跳过 {stats['skipped']}",
    )
    return {
        "status": status,
        "stats": dict(stats),
        "total": total,
        "results": results,
    }
