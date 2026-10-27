from __future__ import annotations

import queue
import threading
import traceback
from collections import defaultdict
from typing import Any

from ..db import dumps, execute, loads, now, query_all, query_one
from .attachment_links import load_attachment_links
from .library_guard import LibraryBusy, hold_library
from .presence_cache import invalidate_library
from .metadata.tmdb import TMDBError, client_from_settings
from .naming import DEFAULT_NAMING, DEFAULT_SCRAPE
from .pipeline import (
    PipelineCancelled,
    _basename,
    _path_under,
    _remap_attachment_paths,
    _remap_item_sources,
    _replace_prefix,
    _skip_dir,
    organize_items,
    scan_library,
)
from .providers import SourceError, build_source, norm
from .renamer import (
    apply_folder_step,
    build_rename_plan,
    collect_folder_steps,
    rename_file_in_place,
)


class _TaskHooks:
    def __init__(self, runner: "TaskRunner", task_id: int) -> None:
        self.runner = runner
        self.task_id = task_id

    def log(self, message: str, level: str = "info") -> None:
        self.runner.log(self.task_id, message, level)

    def update(self, **fields: Any) -> None:
        self.runner._set(self.task_id, **fields)

    def cancelled(self) -> bool:
        return self.runner._cancelled(self.task_id)


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
            if task["library_id"]:
                with hold_library(task["library_id"], timeout=300):
                    self._dispatch(task)
            else:
                self._dispatch(task)
        except TaskCancelled:
            self.log(task_id, "任务已取消", "warn")
            self._set(task_id, status="cancelled", finished_at=now())
        except LibraryBusy as exc:
            self._fail(task_id, f"等待影视库空闲超时: {exc}")
        except Exception as exc:
            self._fail(task_id, f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}")
        finally:
            invalidate_library(task["library_id"])

    def _dispatch(self, task: dict[str, Any]) -> None:
        if task["type"] == "scan":
            self._scan(task)
        elif task["type"] == "organize":
            self._organize(task)
        elif task["type"] == "rename":
            self._rename(task)
        else:
            self._fail(task["id"], f"未知任务类型: {task['type']}")

    # ---------- scan ----------
    def _scan(self, task: dict[str, Any]) -> None:
        scan_library(task["library_id"], _TaskHooks(self, task["id"]))

    # ---------- organize ----------
    def _organize(self, task: dict[str, Any]) -> None:
        payload = loads(task["payload"], {}) or {}
        organize_items(
            task["library_id"],
            _TaskHooks(self, task["id"]),
            item_ids=payload.get("item_ids") or [],
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


TaskCancelled = PipelineCancelled

runner = TaskRunner()
