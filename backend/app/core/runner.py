from __future__ import annotations

import posixpath
import queue
import threading
import time
import traceback
from collections import defaultdict
from typing import Any

from ..db import dumps, execute, get_setting, loads, now, query_all, query_one
from .attachment_links import load_attachment_links
from .presence_cache import invalidate_library
from .matcher import match_item
from .metadata.tmdb import TMDBClient, TMDBError, client_from_settings
from .naming import DEFAULT_NAMING, DEFAULT_SCRAPE
from .organizer import Organizer
from .parser import VIDEO_EXTS, file_ext, parse_media_name
from .providers import FileSource, SourceError, build_source, join, norm, parent_of
from .renamer import (
    apply_folder_step,
    build_rename_plan,
    collect_folder_steps,
    rename_file_in_place,
)
from .scraper import ImageFetcher

SKIP_DIRS = {
    "@eadir", "#recycle", "$recycle.bin", "system volume information",
    ".trash", ".@__thumb", "lost+found", "node_modules", ".git",
    "filmix.trash", "recycle bin", "recycler",
}
GENERIC_SEASON_RE = __import__("re").compile(
    r"^(?:season[ ._-]*\d+|s\d{1,2}|第[一二三四五六七八九十百零两\d]+季|"
    r"(?:cd|disc)[ ._-]*\d+)$",
    __import__("re").I,
)


def _basename(path: str) -> str:
    return posixpath.basename((path or "").rstrip("/"))


def _skip_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered in SKIP_DIRS or lowered.endswith(".trash") or name.startswith(".")


def _path_under(path: str, prefix: str) -> bool:
    path, prefix = norm(path), norm(prefix)
    return bool(prefix) and (path == prefix or path.startswith(prefix + "/"))


def _replace_prefix(path: str, old_prefix: str, new_prefix: str) -> str:
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
        current = norm(row.get("source_path") or "")
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
        video_path = norm(row.get("video_path") or "")
        attachment_path = norm(row.get("attachment_path") or "")
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


class TaskRunner:
    def __init__(self) -> None:
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cineflow-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(0)

    def submit(self, task_id: int) -> None:
        self._queue.put(task_id)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                task_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if not task_id:
                continue
            try:
                self._run(task_id)
            except Exception:
                self._fail(task_id, traceback.format_exc(limit=4))
            finally:
                self._queue.task_done()

    # ---------- task helpers ----------
    def _set(self, task_id: int, **fields: Any) -> None:
        if not fields:
            return
        keys = ", ".join(f"{key}=?" for key in fields)
        execute(f"UPDATE tasks SET {keys} WHERE id=?", (*fields.values(), task_id))

    def log(self, task_id: int, message: str, level: str = "info") -> None:
        execute(
            "INSERT INTO task_logs(task_id, ts, level, message) VALUES(?,?,?,?)",
            (task_id, now(), level, message[:2000]),
        )
        count = query_one("SELECT COUNT(*) AS c FROM task_logs WHERE task_id=?", (task_id,))
        if count and count["c"] > 1200:
            execute(
                "DELETE FROM task_logs WHERE task_id=? AND id IN "
                "(SELECT id FROM task_logs WHERE task_id=? ORDER BY id ASC LIMIT 300)",
                (task_id, task_id),
            )

    def _cancelled(self, task_id: int) -> bool:
        row = query_one("SELECT cancel_requested FROM tasks WHERE id=?", (task_id,))
        return bool(row and row["cancel_requested"]) or self._stop.is_set()

    def _fail(self, task_id: int, error: str) -> None:
        self.log(task_id, f"任务失败: {error.splitlines()[-1][:500] if error else '未知错误'}", "error")
        self._set(task_id, status="failed", error=error[-4000:], finished_at=now(), progress=100)

    def _run(self, task_id: int) -> None:
        task = query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not task:
            return
        if task["status"] == "cancelled":
            return
        self._set(task_id, status="running", started_at=now(), progress=1)
        try:
            if task["type"] == "scan":
                self._scan(task)
            elif task["type"] == "organize":
                self._organize(task)
            elif task["type"] == "rename":
                self._rename(task)
            else:
                self._fail(task_id, f"未知任务类型: {task['type']}")
        except TaskCancelled:
            self.log(task_id, "任务已取消", "warn")
            self._set(task_id, status="cancelled", finished_at=now())
        except Exception as exc:
            self._fail(task_id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}")
        finally:
            invalidate_library(task["library_id"])

    # ---------- scan ----------
    def _scan(self, task: dict[str, Any]) -> None:
        task_id, library_id = task["id"], task["library_id"]
        library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
        if not library:
            raise RuntimeError("影视库不存在")
        source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
        if not source_row:
            raise RuntimeError("文件源不存在")
        source = build_source(source_row)
        library_name = library["name"]
        self.log(task_id, f"开始扫描「{library_name}」")

        min_size_mb = float(get_setting("min_size_mb", 10) or 0)
        max_files = int(get_setting("max_files_per_scan", 3000) or 3000)
        allow_iso = bool(get_setting("allow_iso", True))

        files: list[Any] = []
        queue_dirs: list[tuple[str, int]] = [(library["root_path"], 0)]
        visited = 0
        dirs_read = 0
        missing_dirs = 0
        last_beat = time.monotonic()
        while queue_dirs:
            if self._cancelled(task_id):
                raise TaskCancelled()
            current, depth = queue_dirs.pop(0)
            if depth > 8:
                continue
            dirs_read += 1
            try:
                entries = source.list(current)
            except SourceError as exc:
                missing_dirs += 1
                self.log(task_id, f"跳过目录 {current or '/'}: {exc}", "warn")
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
                    self.log(task_id, f"文件数达到上限 {max_files}，其余文件本次不处理", "warn")
                    queue_dirs.clear()
                    break

            # 网盘目录可能很慢，定期写心跳，避免任务长时间停在 1% 看起来像卡死。
            tick = time.monotonic()
            if tick - last_beat >= 10:
                last_beat = tick
                self._set(
                    task_id,
                    stats=dumps({"visited": visited, "dirs": dirs_read, "found": len(files)}),
                )
                self.log(
                    task_id,
                    f"正在读取目录：已检查 {visited} 个条目，发现 {len(files)} 个视频"
                    f"（已读取 {dirs_read} 个目录）",
                )

        if missing_dirs:
            self.log(
                task_id,
                f"本次跳过 {missing_dirs} 个无法访问的目录（常见于网盘回收站或已删除目录）",
                "warn",
            )
        total = len(files)
        self._set(
            task_id,
            total=total,
            progress=2,
            stats=dumps({"found": total, "visited": visited, "dirs": dirs_read}),
        )
        self.log(task_id, f"扫描到 {total} 个视频文件（共检查 {visited} 个条目）")

        if not total:
            self._set(
                task_id,
                status="success",
                progress=100,
                finished_at=now(),
                stats=dumps(
                    {
                        "found": 0,
                        "matched": 0,
                        "review": 0,
                        "unmatched": 0,
                        "visited": visited,
                        "dirs": dirs_read,
                    }
                ),
            )
            return

        api_key = get_setting("tmdb_api_key", "") or ""
        client = client_from_settings()
        if not api_key:
            self.log(task_id, "未配置 TMDb API Key，只做文件名解析，不做在线匹配", "warn")
        auto_threshold = float(get_setting("auto_threshold", 0.72) or 0.72)
        review_threshold = float(get_setting("review_threshold", 0.5) or 0.5)

        stats = defaultdict(int)
        seen_paths: set[str] = set()
        for index, entry in enumerate(files, start=1):
            if self._cancelled(task_id):
                raise TaskCancelled()
            source_path = entry.path
            seen_paths.add(source_path)
            parsed = parse_media_name(entry.name)
            folder_parsed = self._folder_context(source_path)
            parsed = self._merge_context(parsed, folder_parsed)
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

            match_data: dict[str, Any] = {}
            status = "unmatched"
            error = ""
            if parsed.is_extra:
                status = "ignored"
                error = "疑似预告/花絮/样片"
                stats["ignored"] += 1
                self.log(task_id, f"忽略样本/花絮: {entry.name}", "debug")
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
                    self.log(
                        task_id,
                        f"[{status}] {entry.name} → {best.get('title', '?')} "
                        f"({best.get('year') or '?'}) {int((result.get('confidence') or 0) * 100)}%",
                        "debug",
                    )
                elif index % 10 == 0:
                    self.log(task_id, f"已处理 {index}/{total}", "debug")

            best = match_data.get("best") if match_data else None
            match_key = (
                f"{best.get('media_type')}:{best.get('tmdb_id')}" if best else None
            )
            candidates = match_data.get("candidates") if match_data else []
            match_payload = {**best, "confidence": match_data.get("confidence")} if best else None

            if existing and existing.get("manual"):
                execute(
                    "UPDATE items SET size=?, mtime=?, parsed=?, file_name=?, updated_at=? WHERE id=?",
                    (entry.size, entry.mtime, dumps(parsed.to_dict()), entry.name, now(), existing["id"]),
                )
                stats["manual_kept"] += 1
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
                now(),
            )
            if existing:
                execute(
                    "UPDATE items SET size=?, mtime=?, parsed=?, match=?, candidates=?, status=?, "
                    "error=?, match_key=?, updated_at=? WHERE id=?",
                    (*values, existing["id"]),
                )
            else:
                execute(
                    "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, "
                    "candidates, status, error, match_key, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        library_id, source_path, entry.name, entry.size, entry.mtime,
                        dumps(parsed.to_dict()), dumps(match_payload) if match_payload else None,
                        dumps(candidates or []), status, error or None, match_key, now(), now(),
                    ),
                )

            progress = 2 + (index / total) * 96
            self._set(task_id, progress=round(progress, 1), done=index, stats=dumps(dict(stats)))

        # 标记本次未出现的旧待处理条目
        stale = query_all(
            "SELECT id, source_path FROM items WHERE library_id=? AND status IN ('matched','review','unmatched')",
            (library_id,),
        )
        missing = 0
        for row in stale:
            if row["source_path"] not in seen_paths:
                execute(
                    "UPDATE items SET status='missing', updated_at=? WHERE id=?",
                    (now(), row["id"]),
                )
                missing += 1
        if missing:
            self.log(task_id, f"{missing} 个旧条目在源目录中已不存在，标记为丢失", "warn")

        execute("UPDATE libraries SET last_scan_at=?, updated_at=? WHERE id=?", (now(), now(), library_id))
        stats["missing"] = missing
        stats["visited"] = visited
        stats["dirs"] = dirs_read
        self._set(
            task_id,
            status="success",
            progress=100,
            finished_at=now(),
            stats=dumps(dict(stats)),
            total=total,
            done=len(files),
        )
        self.log(
            task_id,
            f"扫描完成：匹配 {stats['matched']}，待确认 {stats['review']}，"
            f"未识别 {stats['unmatched']}，忽略 {stats['ignored']}",
        )

    def _folder_context(self, source_path: str):
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

    @staticmethod
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

    # ---------- organize ----------
    def _organize(self, task: dict[str, Any]) -> None:
        task_id, library_id = task["id"], task["library_id"]
        library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
        if not library:
            raise RuntimeError("影视库不存在")
        source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
        if not source_row:
            raise RuntimeError("文件源不存在")
        source = build_source(source_row)
        payload = loads(task["payload"], {}) or {}
        item_ids: list[int] = payload.get("item_ids") or []
        if item_ids:
            placeholders = ",".join("?" for _ in item_ids)
            items = query_all(
                f"SELECT * FROM items WHERE library_id=? AND id IN ({placeholders})",
                (library_id, *item_ids),
            )
        else:
            items = query_all(
                "SELECT * FROM items WHERE library_id=? AND status IN ('matched','review') AND match IS NOT NULL",
                (library_id,),
            )
        if not items:
            self._set(task_id, status="success", progress=100, finished_at=now(), stats=dumps({"success": 0}))
            self.log(task_id, "没有可整理的条目")
            return

        total = len(items)
        self._set(task_id, total=total, progress=1)
        self.log(task_id, f"开始整理 {total} 个文件")

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
        )
        target_root = library["target_root"] or ""
        scraped_folders: set[str] = set()
        stats = defaultdict(int)
        warnings: list[str] = []

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
            if self._cancelled(task_id):
                raise TaskCancelled()
            match = loads(item["match"], {}) or {}
            parsed = loads(item["parsed"], {}) or {}
            if not match or not match.get("tmdb_id"):
                stats["skipped"] += 1
                self.log(task_id, f"跳过（没有匹配）: {item['file_name']}", "warn")
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
                self.log(task_id, f"失败: {item['file_name']} - {message}", "error")
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
                    season_details = {
                        number: season_for(int(match["tmdb_id"]), number)
                        for number in sorted(seasons_present)
                    } if media_type == "tv" else {}
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
                )
                stats["success"] += 1
                warnings.extend(result.warnings or [])
                execute(
                    "UPDATE items SET status='organized', target_path=?, error=NULL, updated_at=? WHERE id=?",
                    (result.target_path, now(), item["id"]),
                )
                if result.moved_paths:
                    _remap_attachment_paths(
                        library_id,
                        lambda path, mapping=result.moved_paths: mapping.get(norm(path), norm(path)),
                    )
                attachment_note = ""
                if result.sidecars or result.folder_assets:
                    attachment_note = (
                        f"（附带 {result.sidecars} 个文件"
                        + (f" + {result.folder_assets} 个文件夹级资源" if result.folder_assets else "")
                        + "）"
                    )
                self.log(
                    task_id,
                    f"完成 [{index}/{total}]: {item['file_name']} → {result.target_path}{attachment_note}",
                )
            except (SourceError, OSError) as exc:
                stats["failed"] += 1
                message = str(exc)[:800]
                execute(
                    "UPDATE items SET status='failed', error=?, updated_at=? WHERE id=?",
                    (message, now(), item["id"]),
                )
                self.log(task_id, f"失败 [{index}/{total}]: {item['file_name']} - {message}", "error")

            progress = 1 + (index / total) * 99
            self._set(task_id, progress=round(progress, 1), done=index, stats=dumps(dict(stats)))

        for warning in warnings[:20]:
            self.log(task_id, warning, "warn")
        status = "success"
        if stats["failed"]:
            status = "partial" if stats["success"] else "failed"
        self._set(
            task_id,
            status=status,
            progress=100,
            finished_at=now(),
            stats=dumps(dict(stats)),
            total=total,
            done=total,
        )
        self.log(
            task_id,
            f"整理完成：成功 {stats['success']}，失败 {stats['failed']}，跳过 {stats['skipped']}",
        )

    # ---------- rename only ----------
    def _rename(self, task: dict[str, Any]) -> None:
        task_id, library_id = task["id"], task["library_id"]
        library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
        if not library:
            raise RuntimeError("影视库不存在")
        source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
        if not source_row:
            raise RuntimeError("文件源不存在")
        source = build_source(source_row)
        payload = loads(task["payload"], {}) or {}
        item_ids: list[int] = payload.get("item_ids") or []
        rename_files = bool(payload.get("rename_files", True))
        rename_folders = bool(payload.get("rename_folders", True))
        if not item_ids:
            self._set(task_id, status="success", progress=100, finished_at=now(), stats=dumps({"success": 0}))
            self.log(task_id, "没有可重命名的条目")
            return

        placeholders = ",".join("?" for _ in item_ids)
        items = query_all(
            f"SELECT * FROM items WHERE library_id=? AND id IN ({placeholders})",
            (library_id, *item_ids),
        )
        items = sorted(items, key=lambda row: norm(row.get("source_path") or ""))
        if not items:
            self._set(task_id, status="success", progress=100, finished_at=now(), stats=dumps({"success": 0}))
            self.log(task_id, "没有可重命名的条目")
            return

        total = len(items)
        self._set(task_id, total=total, progress=1)
        self.log(
            task_id,
            "开始仅重命名 "
            f"{total} 个文件（文件：{'是' if rename_files else '否'}，"
            f"文件夹：{'是' if rename_folders else '否'}）",
        )

        naming = {**DEFAULT_NAMING, **(loads(library["naming"], {}) or {})}
        root_path = library["root_path"] or ""
        all_items = query_all(
            "SELECT id, source_path, match_key FROM items WHERE library_id=?",
            (library_id,),
        )
        client = client_from_settings()
        season_cache: dict[tuple[int, int], dict[str, Any]] = {}

        def episode_title_for(match: dict[str, Any], parsed: dict[str, Any]) -> str:
            if not rename_files or not naming.get("episode_title", True):
                return ""
            if (match.get("media_type") or "movie") != "tv":
                return ""
            season_number, episode_number = parsed.get("season"), parsed.get("episode")
            if season_number is None or episode_number is None or not client.configured:
                return ""
            tmdb_id = int(match.get("tmdb_id") or 0)
            if not tmdb_id:
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
            item_match = loads(item.get("match"), {}) or {}
            match_key = norm(
                item.get("match_key")
                or f"{item_match.get('media_type') or 'movie'}:{item_match.get('tmdb_id') or ''}"
            )

            def guard(folder: str) -> bool:
                for other in all_items:
                    if int(other["id"]) == int(item["id"]):
                        continue
                    if not _path_under(other.get("source_path") or "", folder):
                        continue
                    if norm(other.get("match_key") or "") != match_key:
                        return False
                return True

            return guard

        plans = []
        for item in items:
            match = loads(item["match"], {}) or {}
            parsed = loads(item["parsed"], {}) or {}
            plans.append(
                build_rename_plan(
                    source=source,
                    naming=naming,
                    item=item,
                    parsed=parsed,
                    match=match,
                    rename_files=rename_files,
                    rename_folders=rename_folders,
                    root_path=root_path,
                    episode_title=episode_title_for(match, parsed),
                    folder_guard=folder_guard_for(item),
                )
            )
        by_id = {plan.id: plan for plan in plans}

        stats: defaultdict[str, int] = defaultdict(int)
        folder_steps = collect_folder_steps(plans) if rename_folders else []
        for step in folder_steps:
            if self._cancelled(task_id):
                raise TaskCancelled()
            try:
                if apply_folder_step(source, step):
                    stats["folders"] += 1
                    _remap_item_sources(library_id, step.old_path, step.new_path)
                    _remap_attachment_paths(
                        library_id,
                        lambda path, old=step.old_path, new=step.new_path: _replace_prefix(path, old, new),
                    )
                    self.log(
                        task_id,
                        f"文件夹重命名: {step.old_path} → {step.new_path}",
                    )
            except (SourceError, OSError) as exc:
                message = f"文件夹重命名失败: {step.old_path} → {step.new_path} - {exc}"
                stats["failed"] += 1
                self.log(task_id, message, "warn")

        # 文件夹改名会同步更新 item.source_path；文件改名要从最新路径开始。
        manual_links = load_attachment_links(library_id) if rename_files else {}
        for index, item in enumerate(items, start=1):
            if self._cancelled(task_id):
                raise TaskCancelled()
            plan = by_id.get(int(item["id"]))
            if plan is None:
                stats["failed"] += 1
                continue
            if plan.error:
                stats["failed"] += 1
                self.log(task_id, f"跳过 {plan.file_name}: {plan.error}", "warn")
                continue
            for warning in plan.warnings:
                self.log(task_id, f"{plan.file_name}: {warning}", "warn")

            current_row = query_one(
                "SELECT source_path, file_name FROM items WHERE id=?",
                (item["id"],),
            )
            current_path = norm((current_row or {}).get("source_path") or plan.source_path)
            folder_moved = norm(current_path) != norm(plan.source_path)
            file_renamed = False
            if rename_files:
                current_name = _basename(current_path)
                if plan.desired_file_name and norm(plan.desired_file_name) != norm(current_name):
                    try:
                        result = rename_file_in_place(
                            source=source,
                            naming=naming,
                            source_path=current_path,
                            parsed=loads(item["parsed"], {}) or {},
                            desired_name=plan.desired_file_name,
                            manual_links=manual_links,
                        )
                    except (SourceError, OSError) as exc:
                        stats["failed"] += 1
                        self.log(
                            task_id,
                            f"失败 [{index}/{total}]: {plan.file_name} - {exc}",
                            "error",
                        )
                        continue
                    file_renamed = norm(result.target_path) != norm(current_path)
                    if file_renamed:
                        stats["files"] += 1
                        execute(
                            "UPDATE items SET source_path=?, file_name=?, updated_at=? WHERE id=?",
                            (result.target_path, _basename(result.target_path), now(), item["id"]),
                        )
                        _remap_attachment_paths(
                            library_id,
                            lambda path, mapping=result.moved_paths: mapping.get(norm(path), norm(path)),
                        )
                        current_path = result.target_path
                    for warning in result.warnings:
                        self.log(task_id, f"{plan.file_name}: {warning}", "warn")

            if file_renamed or folder_moved:
                stats["success"] += 1
                changes = []
                if folder_moved:
                    changes.append("文件夹")
                if file_renamed:
                    changes.append("文件")
                self.log(
                    task_id,
                    f"完成 [{index}/{total}]: {plan.source_path} → {current_path}"
                    f"（{' + '.join(changes) if changes else '名称已符合规则'}）",
                )
            else:
                stats["skipped"] += 1
                self.log(task_id, f"跳过（名称已符合规则）: {current_path}")

            progress = 1 + (index / total) * 99
            self._set(task_id, progress=round(progress, 1), done=index, stats=dumps(dict(stats)))

        status = "success"
        if stats["failed"]:
            status = "partial" if (stats["success"] or stats["folders"]) else "failed"
        self._set(
            task_id,
            status=status,
            progress=100,
            finished_at=now(),
            stats=dumps(dict(stats)),
            total=total,
            done=total,
        )
        self.log(
            task_id,
            f"仅重命名完成：成功 {stats['success']}，失败 {stats['failed']}，跳过 {stats['skipped']}，"
            f"文件改名 {stats['files']}，文件夹改名 {stats['folders']}",
        )


class TaskCancelled(Exception):
    pass


runner = TaskRunner()
