"""整理后增量同步 strm 文件。

影视库整理到 `target_root` 后，按「整理后路径相对 target_root 的目录」直接
映射到文件源里的两个目录，不扫描整个 strm 库，也不读取 strm 目标库：

    strm 源：{strm_source_root}/{相对目录}
    strm 目标：{strm_target_root}/{相对目录}

路径与 `items.source_path` 一样，是相对文件源根目录的写法（例如
`115/For infuse/...`），因此 OpenList / WebDAV / 本地文件源都能直接用。

只移动与刚整理视频同名的文件（`.strm` 以及同名 nfo / 图片 / 字幕），外加
作品文件夹级的通用素材。strm 生成通常滞后于整理，因此先入队，由后台线程
按需重试；找不到时继续等待，超过时限后记为失败。
"""

from __future__ import annotations

import logging
import posixpath
import threading
from datetime import datetime, timedelta, timezone

from ..db import execute, loads, now, query_all, query_one
from .naming import normalize_collision
from .providers import Entry, FileSource, SourceError, SourceNotFound, build_source, join, norm

logger = logging.getLogger("cinemint.strm")

TICK_SECONDS = 30
# 30 秒一轮，最多等约 2 小时，覆盖 strm 生成器的常规延迟。
MAX_ATTEMPTS = 240
# 整理产生的记录复制成功后再复查约 10 分钟，等 strm 生成器刷新素材。
FORCE_RECHECK_ATTEMPTS = 20
DONE_RETENTION_DAYS = 7
FAILED_RETENTION_DAYS = 30

# 作品文件夹级素材：没有视频文件名前缀，但属于该作品。
FOLDER_ASSETS = {
    "poster.jpg",
    "fanart.jpg",
    "folder.jpg",
    "logo.png",
    "movie.nfo",
    "tvshow.nfo",
    "season.nfo",
}
STEM_BOUNDARY = ".-_ []()"


def normalize_root(value: str) -> str:
    return norm(str(value or ""))


def _is_within(path: str, root: str) -> bool:
    path, root = norm(path), norm(root)
    if not path or not root:
        return False
    return path == root or path.startswith(root + "/")


def validate_roots(source_root: str, target_root: str) -> tuple[str, str]:
    """校验两条文件源内路径；返回规范化后的值，不合法时抛 ValueError。"""
    for label, raw in (("strm 库", source_root), ("strm 目标库", target_root)):
        if ".." in str(raw or "").replace("\\", "/").split("/"):
            raise ValueError(f"{label}路径不能包含 ..")
    source = normalize_root(source_root)
    target = normalize_root(target_root)
    if not source or not target:
        raise ValueError("请选择 strm 库和 strm 目标库")
    if source == target:
        raise ValueError("strm 库和 strm 目标库不能是同一目录")
    if _is_within(target, source) or _is_within(source, target):
        raise ValueError("strm 库和 strm 目标库不能互相包含")
    return source, target


def relative_path_for(path: str, library_target_root: str) -> str:
    """路径 → 相对 target_root 的部分（strm 库与目标库共用同一层级）。"""
    path = norm(path)
    root = norm(library_target_root)
    if root and path == root:
        return ""
    if root and path.startswith(root + "/"):
        return path[len(root) + 1 :]
    return path


def relative_dir_for(target_path: str, library_target_root: str) -> str:
    return posixpath.dirname(relative_path_for(target_path, library_target_root))


def file_stem_for(path: str) -> str:
    return posixpath.splitext(posixpath.basename(norm(path)))[0]


def _matches(name: str, stem: str) -> bool:
    if name.lower() in FOLDER_ASSETS:
        return True
    if not stem:
        return False
    candidate = posixpath.splitext(name)[0]
    if candidate == stem:
        return True
    return (
        candidate.startswith(stem)
        and len(candidate) > len(stem)
        and candidate[len(stem)] in STEM_BOUNDARY
    )


def _up_to_date(source_entry: Entry, target_entry: Entry) -> bool:
    """大小一致且目标不比源旧时视为已经同步，避免每集都重复复制同一批素材。"""
    if source_entry.size != target_entry.size:
        return False
    if source_entry.mtime and target_entry.mtime:
        return target_entry.mtime + 0.001 >= source_entry.mtime
    return True


def _same_content(source: FileSource, source_entry: Entry, target_entry: Entry) -> bool:
    """同名策略为 (1) 时用来判断目标是否已经是同样内容，避免重复生成副本。"""
    if source_entry.size != target_entry.size:
        return False
    try:
        return source.read(source_entry.path) == source.read(target_entry.path)
    except (SourceError, OSError):
        return False


def _next_free_name(names: set[str], name: str) -> str:
    stem, ext = posixpath.splitext(name)
    for index in range(1, 100):
        candidate = f"{stem} ({index}){ext}"
        if candidate not in names:
            return candidate
    return ""


def _has_same_content(
    source: FileSource,
    source_entry: Entry,
    existing: dict[str, Entry],
    name: str,
) -> bool:
    """(1) 策略下，同名及已有 (1)/(2)… 里只要有一份相同内容就视为已同步。"""
    stem, ext = posixpath.splitext(name)
    for index in range(0, 100):
        candidate = name if index == 0 else f"{stem} ({index}){ext}"
        previous = existing.get(candidate)
        if previous is not None and _same_content(source, source_entry, previous):
            return True
    return False


def sync_one(
    source: FileSource,
    source_root: str,
    target_root: str,
    relative_dir: str,
    stem: str,
    collision: str = "suffix",
    delete_missing: bool = False,
    force: bool = False,
) -> tuple[str, str, int]:
    """尝试同步一个整理结果（复制，不移动 strm 库里的源文件）。

    同名策略与影视库整理一致：`suffix` 生成 `(1)`、`overwrite` 覆盖、
    `skip` 保留目标旧文件。`delete_missing` 开启时，strm 库没有、但目标库
    里仍存在的同范围旧素材会被删除。

    返回 `(status, error, copied)`，status 为 `done` / `waiting` / `error`。
    waiting 表示 strm 目录或对应文件还没生成，交给下一轮重试。
    """
    policy = normalize_collision(collision)
    relative_dir = norm(relative_dir)
    if ".." in relative_dir.split("/"):
        return "error", "strm 相对目录不合法", 0
    source_dir = join(source_root, relative_dir)
    target_dir = join(target_root, relative_dir)
    try:
        entries = source.list(source_dir)
    except SourceNotFound:
        return "waiting", "", 0
    except SourceError as exc:
        return "error", f"读取 strm 库失败：{exc}", 0
    candidates = [entry for entry in entries if not entry.is_dir and _matches(entry.name, stem)]

    try:
        existing = {entry.name: entry for entry in source.list(target_dir) if not entry.is_dir}
    except SourceNotFound:
        existing = {}
    except SourceError as exc:
        return "error", f"读取 strm 目标目录失败：{exc}", 0

    copied = 0
    removed = 0
    errors: list[str] = []
    try:
        source.mkdir(target_dir)
    except SourceError as exc:
        return "error", f"创建 strm 目标目录失败：{exc}", 0

    if delete_missing and existing:
        # 只处理与本次同步同范围的文件，避免误删目标库其他内容。
        wanted = {entry.name for entry in candidates}
        for name, entry in list(existing.items()):
            if name in wanted or not _matches(name, stem):
                continue
            try:
                source.delete(entry.path)
                existing.pop(name, None)
                removed += 1
            except (SourceError, OSError) as exc:
                errors.append(f"{name}: 删除失败 {exc}")

    if not candidates:
        if removed:
            logger.info("strm 同步：%s 清理目标旧文件 %d 个", target_dir, removed)
            return "done", "；".join(errors)[:800], removed
        return "waiting", "", 0

    for entry in candidates:
        previous = existing.get(entry.name)
        destination = entry.name
        if previous is not None:
            if policy == "skip":
                continue
            if policy == "overwrite":
                if not force and _up_to_date(entry, previous):
                    continue
            else:
                if _has_same_content(source, entry, existing, entry.name):
                    continue
                destination = _next_free_name(set(existing), entry.name)
                if not destination:
                    errors.append(f"{entry.name}: 目标重名过多")
                    continue
        # 复制而不是移动：strm 库里的源文件保留，目标缺失或过期时能重新补。
        try:
            source.write(join(target_dir, destination), source.read(entry.path))
            existing[destination] = Entry(
                name=destination,
                path=join(target_dir, destination),
                is_dir=False,
                size=entry.size,
                mtime=entry.mtime,
            )
            copied += 1
        except (SourceError, OSError) as exc:
            errors.append(f"{entry.name}: {exc}")
    changes = copied + removed
    if errors and not changes:
        return "error", "；".join(errors)[:800], 0
    if changes:
        logger.info(
            "strm 同步：%s → %s（复制 %d，清理 %d）", source_dir, target_dir, copied, removed
        )
    return "done", "；".join(errors)[:800], changes


def enqueue(library_id: int, target_path: str, show_dir: str = "") -> bool:
    """整理成功后登记同步；没有开启或路径不完整时直接忽略。

    `show_dir` 是剧集的作品目录（Season 上一级）。剧集的 tvshow.nfo、poster、
    fanart、logo 等素材放在作品目录，不在季目录里，需要单独同步；电影的作品
    级素材就在视频同目录，已由视频记录覆盖。
    """
    if not target_path:
        return False
    library = query_one(
        "SELECT id, strm_sync_enabled, strm_source_root, strm_target_root, target_root "
        "FROM libraries WHERE id=?",
        (library_id,),
    )
    if not library or not int(library["strm_sync_enabled"] or 0):
        return False
    if not library["strm_source_root"] or not library["strm_target_root"]:
        return False
    queue_show_dir(library, show_dir, target_path)
    relative_dir = relative_dir_for(target_path, library["target_root"] or "")
    stem = file_stem_for(target_path)
    execute(
        "INSERT INTO strm_sync_queue(library_id, target_path, relative_dir, file_stem, "
        "status, attempts, force, last_error, created_at, updated_at) "
        "VALUES(?,?,?,?, 'pending', 0, 1, NULL, ?, ?) "
        "ON CONFLICT(library_id, target_path) DO UPDATE SET "
        "relative_dir=excluded.relative_dir, file_stem=excluded.file_stem, "
        "status='pending', attempts=0, force=1, last_error=NULL, "
        "updated_at=excluded.updated_at",
        (library_id, target_path, relative_dir, stem, now(), now()),
    )
    return True


def queue_show_dir(library: dict, show_dir: str, target_path: str) -> None:
    """登记作品目录（剧集根）的素材同步；已经在季目录里或没有作品目录时跳过。"""
    show_dir = norm(show_dir)
    if not show_dir:
        return
    target_root = library["target_root"] or ""
    show_relative = relative_path_for(show_dir, target_root)
    if show_relative == relative_dir_for(target_path, target_root):
        return
    existing = query_one(
        "SELECT status FROM strm_sync_queue WHERE library_id=? AND target_path=?",
        (library["id"], show_dir),
    )
    # 还在等待时沿用原记录；已完成 / 失败时重新排队，这样作品被删掉后
    # 重新整理新集时还能把缺失的 tvshow.nfo / 海报等补回去。
    if existing and existing["status"] == "pending":
        return
    execute(
        "INSERT INTO strm_sync_queue(library_id, target_path, relative_dir, file_stem, "
        "status, attempts, force, last_error, created_at, updated_at) "
        "VALUES(?,?,?, '', 'pending', 0, 1, NULL, ?, ?) "
        "ON CONFLICT(library_id, target_path) DO UPDATE SET "
        "relative_dir=excluded.relative_dir, file_stem='', status='pending', "
        "attempts=0, force=1, last_error=NULL, updated_at=excluded.updated_at",
        (library["id"], show_dir, show_relative, now(), now()),
    )


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _purge() -> None:
    execute(
        "DELETE FROM strm_sync_queue WHERE status='done' AND updated_at < ?",
        (_cutoff(DONE_RETENTION_DAYS),),
    )
    execute(
        "DELETE FROM strm_sync_queue WHERE status='failed' AND updated_at < ?",
        (_cutoff(FAILED_RETENTION_DAYS),),
    )


def _mark(row_id: int, status: str, error: str | None = None, attempts: int | None = None) -> None:
    if attempts is None:
        execute(
            "UPDATE strm_sync_queue SET status=?, last_error=?, updated_at=? WHERE id=?",
            (status, error, now(), row_id),
        )
    else:
        execute(
            "UPDATE strm_sync_queue SET status=?, last_error=?, attempts=?, updated_at=? WHERE id=?",
            (status, error, attempts, now(), row_id),
        )


def _source_for(
    cache: dict[int, FileSource | SourceError], source_id: int
) -> FileSource:
    cached = cache.get(source_id)
    if isinstance(cached, FileSource):
        return cached
    if isinstance(cached, SourceError):
        raise cached
    source_row = query_one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not source_row:
        error = SourceError("文件源不存在")
        cache[source_id] = error
        raise error
    try:
        source = build_source(source_row)
    except Exception as exc:  # 文件源配置无法解析时按错误重试，不阻塞其他库
        error = SourceError(str(exc))
        cache[source_id] = error
        raise error
    cache[source_id] = source
    return source


def process_pending(limit: int = 200) -> dict[str, int]:
    """处理待同步记录；只在单进程内由后台线程调用。"""
    rows = query_all(
        "SELECT q.id, q.relative_dir, q.file_stem, q.attempts, q.force, l.strm_sync_enabled, "
        "l.strm_source_root, l.strm_target_root, l.strm_delete_missing, l.naming, l.source_id "
        "FROM strm_sync_queue q JOIN libraries l ON l.id=q.library_id "
        "WHERE q.status='pending' ORDER BY q.id LIMIT ?",
        (limit,),
    )
    stats = {"done": 0, "waiting": 0, "failed": 0}
    sources: dict[int, FileSource | SourceError] = {}
    for row in rows:
        if not int(row["strm_sync_enabled"] or 0):
            _mark(row["id"], "failed", "strm 同步已关闭")
            stats["failed"] += 1
            continue
        if not row["strm_source_root"] or not row["strm_target_root"]:
            _mark(row["id"], "failed", "未设置 strm 库路径")
            stats["failed"] += 1
            continue
        previous_attempts = int(row["attempts"] or 0)
        # 只有整理后的第一次复制按「覆盖」强制写入；后续复查只在源更新时再复制。
        force_now = bool(row["force"]) and previous_attempts == 0
        naming = loads(row["naming"], {}) or {}
        try:
            source = _source_for(sources, int(row["source_id"]))
            status, error, _moved = sync_one(
                source,
                row["strm_source_root"],
                row["strm_target_root"],
                row["relative_dir"],
                row["file_stem"],
                collision=str(naming.get("collision") or ""),
                delete_missing=bool(row["strm_delete_missing"]),
                force=force_now,
            )
        except SourceError as exc:
            status, error = "error", f"文件源不可用：{exc}"
        attempts = previous_attempts + 1
        if status == "done":
            if bool(row["force"]) and attempts <= FORCE_RECHECK_ATTEMPTS:
                # 复制成功后继续观察一小段时间，strm 生成器刷新后能自动再覆盖一次。
                _mark(row["id"], "pending", error or None, attempts)
                stats["waiting"] += 1
            else:
                _mark(row["id"], "done", error or None, attempts)
                stats["done"] += 1
        elif attempts >= MAX_ATTEMPTS:
            _mark(
                row["id"],
                "failed",
                error or "strm 库中暂未找到对应文件",
                attempts,
            )
            stats["failed"] += 1
        else:
            _mark(row["id"], "pending", error or None, attempts)
            stats["waiting"] += 1
    return stats


class StrmSyncer:
    def __init__(self, tick_seconds: float = TICK_SECONDS) -> None:
        self._tick_seconds = max(5.0, float(tick_seconds))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="cinemint-strm-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    def _loop(self) -> None:
        purge_counter = 0
        while not self._stop.is_set():
            try:
                if purge_counter <= 0:
                    _purge()
                    purge_counter = 120
                purge_counter -= 1
                process_pending()
            except Exception:
                logger.exception("strm 同步轮询失败")
            self._stop.wait(self._tick_seconds)


strm_syncer = StrmSyncer()
