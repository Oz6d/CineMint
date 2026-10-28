"""待整理目录自动探测。

OpenList / WebDAV 没有变更推送，这里按固定间隔做一次轻量检查：
刷新目录缓存、列出视频文件并与上次的指纹比较，发现变化才提交普通扫描任务。
"""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from ..db import dumps, execute, get_setting, now, query_all, query_one
from .openlist import OpenListError, refresh_directory
from .parser import VIDEO_EXTS, file_ext
from .pipeline import _skip_dir
from .providers import SourceError, SourceNotFound, build_source
from .runner import runner

logger = logging.getLogger("cineflow.auto_scan")

MAX_DEPTH = 3
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
DEFAULT_INTERVAL_MINUTES = 15
TICK_SECONDS = 30.0
EMPTY_SIGNATURE = hashlib.sha256(b"").hexdigest()


def interval_minutes() -> int:
    try:
        value = int(
            get_setting("auto_scan_interval_minutes", DEFAULT_INTERVAL_MINUTES)
            or DEFAULT_INTERVAL_MINUTES
        )
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_MINUTES
    return max(MIN_INTERVAL_MINUTES, min(value, MAX_INTERVAL_MINUTES))


def collect_signature(
    source: Any,
    root: str,
    *,
    min_size_mb: float,
    allow_iso: bool,
    max_files: int,
    max_depth: int = MAX_DEPTH,
) -> str:
    """按扫描同样的过滤规则收集文件，返回「路径 + 大小」的指纹。

    根目录打不开时直接抛出，交给调用方决定是否触发扫描；
    子目录临时出错则跳过，避免一个坏目录让整轮检查失败。
    """
    queue: list[tuple[str, int]] = [(root, 0)]
    lines: list[str] = []
    while queue:
        current, depth = queue.pop(0)
        if depth > max_depth:
            continue
        try:
            entries = source.list(current)
        except (SourceNotFound, SourceError):
            if current == root:
                raise
            continue
        for entry in entries:
            if entry.is_dir:
                if depth + 1 > max_depth or _skip_dir(entry.name):
                    continue
                queue.append((entry.path, depth + 1))
                continue
            ext = file_ext(entry.name)
            if ext not in VIDEO_EXTS:
                continue
            if ext == ".iso" and not allow_iso:
                continue
            if entry.size and entry.size < min_size_mb * 1024 * 1024:
                continue
            lines.append(f"{entry.path}|{entry.size}")
            if len(lines) >= max_files:
                queue.clear()
                break
    lines.sort()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _state(library_id: int) -> dict[str, Any] | None:
    return query_one("SELECT * FROM library_auto_scan WHERE library_id=?", (library_id,))


def _save_state(
    library_id: int,
    *,
    fingerprint: str | None,
    checked_at: str,
    last_task_id: int | None,
    last_error: str,
) -> None:
    execute(
        "INSERT INTO library_auto_scan"
        "(library_id, fingerprint, checked_at, last_task_id, last_error, updated_at) "
        "VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(library_id) DO UPDATE SET fingerprint=excluded.fingerprint, "
        "checked_at=excluded.checked_at, last_task_id=excluded.last_task_id, "
        "last_error=excluded.last_error, updated_at=excluded.updated_at",
        (library_id, fingerprint, checked_at, last_task_id, last_error, now()),
    )


def _recent(state: dict[str, Any] | None, interval_seconds: int) -> bool:
    if not state or not state.get("checked_at"):
        return False
    try:
        checked = datetime.fromisoformat(str(state["checked_at"]))
    except ValueError:
        return False
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - checked).total_seconds() < interval_seconds


def _busy(library_id: int) -> bool:
    if query_one(
        "SELECT id FROM tasks WHERE library_id=? AND status IN ('queued','running') LIMIT 1",
        (library_id,),
    ):
        return True
    return bool(
        query_one(
            "SELECT r.id FROM subscription_runs r "
            "JOIN subscriptions s ON s.id=r.subscription_id "
            "WHERE s.library_id=? AND r.status IN ('queued','running') LIMIT 1",
            (library_id,),
        )
    )


def _enqueue_scan(library_id: int) -> int:
    return execute(
        "INSERT INTO tasks(library_id, type, status, payload, created_at) VALUES(?,?,?,?,?)",
        (library_id, "scan", "queued", dumps({"trigger": "auto"}), now()),
    )


def check_library(
    library: dict[str, Any],
    *,
    interval_seconds: int | None = None,
    force: bool = False,
    submit: Callable[[int], None] | None = None,
) -> str:
    """检查一个影视库；返回 not_due / busy / unchanged / scanned / error。"""
    library_id = int(library["id"])
    if interval_seconds is None:
        interval_seconds = interval_minutes() * 60
    state = _state(library_id)
    if not force and _recent(state, interval_seconds):
        return "not_due"
    if _busy(library_id):
        # 有任务占用时只顺延检查时间，避免每个 tick 都去探测。
        _save_state(
            library_id,
            fingerprint=state.get("fingerprint") if state else None,
            checked_at=now(),
            last_task_id=state.get("last_task_id") if state else None,
            last_error=state.get("last_error") if state else "",
        )
        return "busy"

    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    if not source_row:
        _save_state(
            library_id,
            fingerprint=state.get("fingerprint") if state else None,
            checked_at=now(),
            last_task_id=state.get("last_task_id") if state else None,
            last_error="文件源不存在",
        )
        return "error"

    root = library["root_path"] or ""
    try:
        refresh_directory(source_row, root)
    except OpenListError as exc:
        logger.warning("自动扫描刷新目录失败（%s）：%s", library.get("name"), exc)

    min_size_mb = float(get_setting("min_size_mb", 10) or 0)
    max_files = int(get_setting("max_files_per_scan", 3000) or 3000)
    allow_iso = bool(get_setting("allow_iso", True))
    try:
        source = build_source(source_row)
        signature = collect_signature(
            source,
            root,
            min_size_mb=min_size_mb,
            allow_iso=allow_iso,
            max_files=max_files,
        )
    except SourceNotFound:
        # 库根已不存在：按空目录处理，让扫描去标记丢失。
        signature = EMPTY_SIGNATURE
    except Exception as exc:
        logger.warning("自动扫描探测失败（%s）：%s", library.get("name"), exc)
        _save_state(
            library_id,
            fingerprint=state.get("fingerprint") if state else None,
            checked_at=now(),
            last_task_id=state.get("last_task_id") if state else None,
            last_error=str(exc)[:500],
        )
        return "error"

    previous = state.get("fingerprint") if state else None
    task_id = state.get("last_task_id") if state else None
    result = "unchanged"
    if previous is None or previous != signature:
        task_id = _enqueue_scan(library_id)
        (submit or runner.submit)(task_id)
        result = "scanned"
        logger.info(
            "自动扫描已提交：%s（task #%s）", library.get("name"), task_id
        )
    _save_state(
        library_id,
        fingerprint=signature,
        checked_at=now(),
        last_task_id=task_id,
        last_error="",
    )
    return result


def run_once(*, force: bool = False) -> list[dict[str, Any]]:
    """执行一轮检查；开关关闭时直接返回。"""
    if not bool(get_setting("auto_scan_enabled", False)):
        return []
    library_interval = interval_minutes() * 60
    results: list[dict[str, Any]] = []
    for library in query_all(
        "SELECT * FROM libraries WHERE is_demo=0 AND enabled=1 ORDER BY id ASC"
    ):
        try:
            status = check_library(
                library, interval_seconds=library_interval, force=force
            )
        except Exception:
            logger.exception("自动扫描检查失败：%s", library.get("name"))
            status = "error"
        results.append({"library_id": library["id"], "status": status})
    return results


class AutoScanner:
    def __init__(self, tick_seconds: float = TICK_SECONDS) -> None:
        self._tick_seconds = max(5.0, float(tick_seconds))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="cineflow-auto-scan", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                run_once()
            except Exception:
                logger.exception("自动扫描轮询失败")
            self._stop.wait(self._tick_seconds)


auto_scanner = AutoScanner()
