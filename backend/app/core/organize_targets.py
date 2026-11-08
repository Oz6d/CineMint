"""作品级整理位置：手动指定分类目录或作品文件夹，并迁移已整理文件。

自动分类按 TMDb 的地区 / 题材猜目录，猜错后无法记住正确答案。这里按
「影视库 + TMDb 作品」保存一个相对 target_root 的位置，整理器优先使用；
用户也可以把已经整理到错误位置的文件整体或逐集迁过去。
"""

from __future__ import annotations

import posixpath
from typing import Any, Callable

from ..db import db, execute, loads, now, query_all
from .attachment_links import load_attachment_links
from .naming import DEFAULT_NAMING, DEFAULT_SCRAPE
from .organizer import Organizer, remove_empty_source_dirs
from .providers import FileSource, SourceError, join, norm, parent_of, safe_relative

TARGET_MODES = ("category", "show")
MAX_MOVE_DEPTH = 32


def work_key(match: dict[str, Any] | None) -> str:
    match = match or {}
    tmdb_id = match.get("tmdb_id")
    media_type = match.get("media_type") or "movie"
    if not tmdb_id:
        return ""
    return f"{media_type}:{int(tmdb_id)}"


def normalize_target_path(path: str, mode: str = "category") -> str:
    """把用户选择的位置规范成 target_root 内的相对路径。"""
    if mode not in TARGET_MODES:
        raise ValueError("整理位置类型不合法")
    try:
        value = safe_relative(path or "")
    except SourceError as exc:
        raise ValueError(str(exc)) from exc
    if value and any(part.startswith(".") for part in value.split("/")):
        raise ValueError("不能选择隐藏目录或回收目录")
    if mode == "show" and not value:
        raise ValueError("请选择作品文件夹")
    return value


def load_targets(library_id: int) -> dict[str, dict[str, Any]]:
    rows = query_all(
        "SELECT match_key, path, mode, source, confidence, reason "
        "FROM organize_targets WHERE library_id=?",
        (library_id,),
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        mode = row["mode"] if row["mode"] in TARGET_MODES else "category"
        try:
            path = normalize_target_path(row["path"], mode)
        except ValueError:
            continue
        result[row["match_key"]] = {
            "path": path,
            "mode": mode,
            "source": row.get("source") or "manual",
            "confidence": row.get("confidence"),
            "reason": row.get("reason") or "",
        }
    return result


def get_target(library_id: int, key: str) -> dict[str, Any] | None:
    return load_targets(library_id).get(key)


def save_target(
    library_id: int,
    key: str,
    path: str,
    mode: str,
    *,
    source: str = "manual",
    confidence: float | None = None,
    reason: str = "",
) -> dict[str, Any]:
    if not key:
        raise ValueError("该文件还没有匹配到作品")
    value = normalize_target_path(path, mode)
    origin = "ai" if source == "ai" else "manual"
    stamp = now()
    execute(
        "INSERT INTO organize_targets(library_id, match_key, path, mode, source, confidence, "
        "reason, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(library_id, match_key) DO UPDATE SET "
        "path=excluded.path, mode=excluded.mode, source=excluded.source, "
        "confidence=excluded.confidence, reason=excluded.reason, "
        "updated_at=excluded.updated_at",
        (library_id, key, value, mode, origin, confidence, reason[:300], stamp, stamp),
    )
    return {
        "match_key": key,
        "path": value,
        "mode": mode,
        "source": origin,
        "confidence": confidence,
        "reason": reason[:300],
    }


def delete_target(library_id: int, key: str) -> bool:
    before = query_all(
        "SELECT id FROM organize_targets WHERE library_id=? AND match_key=?",
        (library_id, key),
    )
    execute(
        "DELETE FROM organize_targets WHERE library_id=? AND match_key=?",
        (library_id, key),
    )
    return bool(before)


def validate_target_directory(
    source: FileSource,
    target_root: str,
    path: str,
    mode: str,
) -> str:
    """确认选择的位置位于 target_root 内且真实存在。"""
    root = norm(target_root or "")
    if not root:
        raise ValueError("请先在影视库设置里填写整理后目录")
    value = normalize_target_path(path, mode)
    full = join(root, value) if value else root
    info = source.stat(full)
    if not info:
        raise ValueError("选择的目录不存在，请重新选择")
    if not info.is_dir:
        raise ValueError("选择的位置不是文件夹")
    return value


def _is_within(path: str, root: str) -> bool:
    path, root = norm(path), norm(root)
    return bool(root) and (path == root or path.startswith(root + "/"))


def _current_source_path(
    source: FileSource,
    target_root: str,
    stored: str,
) -> str:
    """把数据库里保存的目标路径还原成源内完整路径。

    正常数据带整理根目录；演示库等旧数据只保存根目录内的相对路径，
    用文件是否存在来兼容两种写法。
    """
    value = norm(stored)
    if not value:
        return ""
    try:
        if source.exists(value):
            return value
    except SourceError:
        return ""
    root = norm(target_root or "")
    if root and not _is_within(value, root):
        candidate = join(root, value)
        try:
            if source.exists(candidate):
                return candidate
        except SourceError:
            return ""
    return ""


def _current_show_folder(
    organizer: Organizer,
    parsed: dict[str, Any],
    match: dict[str, Any],
    target_path: str,
) -> str:
    """从已整理文件路径反推作品文件夹（剧集时去掉季文件夹）。"""
    folder = parent_of(target_path)
    if (match.get("media_type") or "movie") != "tv" or parsed.get("season") is None:
        return folder
    try:
        season_name = organizer._season_folder_name(int(parsed["season"]), match)
    except (TypeError, ValueError):
        return folder
    season_name = norm(season_name)
    if season_name and posixpath.basename(folder) == season_name:
        return parent_of(folder)
    return folder


def _stored_variants(value: str, target_root: str) -> list[tuple[str, bool]]:
    """数据库里的目标路径可能带整理根目录，也可能相对它（演示库旧数据）。

    返回 (完整路径, 是否是相对整理根目录的写法)。
    """
    value = norm(value)
    if not value:
        return []
    root = norm(target_root or "")
    if not root or _is_within(value, root):
        return [(value, False)]
    return [(value, False), (join(root, value), True)]


def _replace_stored(
    value: str,
    old: str,
    new: str,
    recursive: bool,
    target_root: str,
) -> tuple[bool, str]:
    """按数据库里保存的路径写法替换；找不到匹配时原样返回。"""
    for full, relative_form in _stored_variants(value, target_root):
        matched = _is_within(full, old) if recursive else full == old
        if not matched:
            continue
        replaced = new + full[len(old):] if recursive else new
        if relative_form and _is_within(replaced, target_root):
            replaced = replaced[len(target_root):].lstrip("/")
        return True, replaced
    return False, value


def _remap_paths(
    library_id: int,
    target_root: str,
    moves: list[tuple[str, str, bool]],
) -> int:
    """迁移后同步 items.target_path、附件关联和订阅运行记录。"""
    updated = 0
    root = norm(target_root or "")
    with db() as conn:
        for old, new, recursive in moves:
            old, new = norm(old), norm(new)
            if not old or old == new:
                continue

            items = conn.execute(
                "SELECT id, target_path FROM items WHERE library_id=? "
                "AND target_path IS NOT NULL",
                (library_id,),
            ).fetchall()
            for row in items:
                matched, replaced = _replace_stored(
                    norm(row["target_path"]), old, new, recursive, root
                )
                if not matched:
                    continue
                conn.execute(
                    "UPDATE items SET target_path=?, updated_at=? WHERE id=?",
                    (replaced, now(), row["id"]),
                )
                updated += 1

            links = conn.execute(
                "SELECT id, video_path, attachment_path FROM attachment_links "
                "WHERE library_id=?",
                (library_id,),
            ).fetchall()
            for row in links:
                video = norm(row["video_path"])
                attachment = norm(row["attachment_path"])
                video_hit, new_video = _replace_stored(
                    video, old, new, recursive, root
                )
                attachment_hit, new_attachment = _replace_stored(
                    attachment, old, new, recursive, root
                )
                if not video_hit and not attachment_hit:
                    continue
                if new_video == video and new_attachment == attachment:
                    continue
                conflict = conn.execute(
                    "SELECT id FROM attachment_links WHERE library_id=? "
                    "AND attachment_path=? AND id<>?",
                    (library_id, new_attachment, row["id"]),
                ).fetchone()
                if conflict:
                    conn.execute(
                        "DELETE FROM attachment_links WHERE id=?", (conflict["id"],)
                    )
                conn.execute(
                    "UPDATE attachment_links SET video_path=?, attachment_path=? "
                    "WHERE id=?",
                    (new_video, new_attachment, row["id"]),
                )
                updated += 1

            entries = conn.execute(
                "SELECT id, target_path FROM subscription_entries "
                "WHERE target_path IS NOT NULL AND subscription_id IN "
                "(SELECT id FROM subscriptions WHERE library_id=?)",
                (library_id,),
            ).fetchall()
            for row in entries:
                matched, replaced = _replace_stored(
                    norm(row["target_path"]), old, new, recursive, root
                )
                if not matched:
                    continue
                conn.execute(
                    "UPDATE subscription_entries SET target_path=?, updated_at=? "
                    "WHERE id=?",
                    (replaced, now(), row["id"]),
                )
                updated += 1
    return updated


def _record_move(
    moves: list[tuple[str, str, bool]],
    source: FileSource,
    old: str,
    new: str,
    info,
) -> None:
    moves.append((old, new, bool(info and info.is_dir)))


def _merge_move(
    source: FileSource,
    src: str,
    dst: str,
    moves: list[tuple[str, str, bool]],
    skipped: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    depth: int = 0,
) -> None:
    """把 src 移动到 dst；目标已存在时递归并入，绝不覆盖。"""
    if depth > MAX_MOVE_DEPTH:
        failed.append({"path": src, "error": "目录层级过深，已停止移动"})
        return
    info = source.stat(src)
    if not info:
        return
    target = source.stat(dst)
    if not target:
        try:
            source.move(src, dst)
            _record_move(moves, source, src, dst, info)
        except SourceError as exc:
            # WebDAV 有时移动成功仍返回错误，以实际状态为准。
            if not source.exists(src) and source.exists(dst):
                _record_move(moves, source, src, dst, info)
            else:
                failed.append({"path": src, "error": str(exc)})
        return
    if not info.is_dir or not target.is_dir:
        skipped.append({"path": src, "reason": "目标已有同名文件，保留原文件"})
        return
    try:
        entries = source.list(src)
    except SourceError as exc:
        failed.append({"path": src, "error": str(exc)})
        return
    for entry in entries:
        _merge_move(
            source,
            entry.path,
            join(dst, entry.name),
            moves,
            skipped,
            failed,
            depth + 1,
        )
    try:
        if not source.list(src):
            source.delete(src)
    except SourceError:
        pass


def _episode_sidecars(
    source: FileSource,
    folder: str,
    current: str,
    attachment_paths: set[str],
) -> list[Any]:
    stem = posixpath.splitext(posixpath.basename(current))[0]
    result = []
    try:
        entries = source.list(folder)
    except SourceError:
        return result
    for entry in entries:
        if entry.is_dir or norm(entry.path) == norm(current):
            continue
        name = entry.name
        if (
            name.startswith(stem + ".")
            or name.startswith(stem + "-")
            or norm(entry.path) in attachment_paths
        ):
            result.append(entry)
    return result


def _move_one_file(
    source: FileSource,
    src: str,
    dst: str,
    moves: list[tuple[str, str, bool]],
    skipped: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> None:
    info = source.stat(src)
    if not info:
        return
    if source.exists(dst):
        skipped.append({"path": src, "reason": "目标已有同名文件，保留原文件"})
        return
    try:
        source.move(src, dst)
        _record_move(moves, source, src, dst, info)
    except SourceError as exc:
        if not source.exists(src) and source.exists(dst):
            _record_move(moves, source, src, dst, info)
        else:
            failed.append({"path": src, "error": str(exc)})


def relocate_item(
    library: dict[str, Any],
    source: FileSource,
    item: dict[str, Any],
    target_dir: str,
    refresh_dir: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """把单个已整理文件（及其字幕 / 音轨）移动到指定文件夹。"""
    library_id = int(library["id"])
    target_root = norm(library["target_root"] or "")
    relative = normalize_target_path(target_dir, "show")
    result: dict[str, Any] = {
        "moved": [],
        "skipped": [],
        "failed": [],
        "removed_dirs": [],
        "warnings": [],
        "updated": 0,
        "checked": 1,
    }
    if not target_root:
        result["checked"] = 0
        return result
    current = _current_source_path(source, target_root, item.get("target_path") or "")
    if not current or not _is_within(current, target_root):
        result["skipped"].append(
            {"path": norm(item.get("target_path") or ""), "reason": "文件不在整理后目录内，未移动"}
        )
        return result
    new_folder = join(target_root, relative)
    new_target = join(new_folder, posixpath.basename(current))
    if norm(current) == norm(new_target):
        return result

    links = query_all(
        "SELECT video_path, attachment_path FROM attachment_links WHERE library_id=?",
        (library_id,),
    )
    attachment_paths = {
        norm(link["attachment_path"])
        for link in links
        if norm(link["video_path"]) == norm(current)
    }
    sidecars = _episode_sidecars(
        source,
        parent_of(current),
        current,
        attachment_paths,
    )
    moves: list[tuple[str, str, bool]] = []
    old_files: list[str] = []
    source.mkdir(new_folder)
    _move_one_file(
        source,
        current,
        new_target,
        moves,
        result["skipped"],
        result["failed"],
    )
    if source.exists(current):
        # 视频没有移动成功时不要把字幕 / NFO 单独搬走。
        return result
    old_files.append(current)
    for sidecar in sidecars:
        _move_one_file(
            source,
            sidecar.path,
            join(new_folder, sidecar.name),
            moves,
            result["skipped"],
            result["failed"],
        )
        if not source.exists(sidecar.path):
            old_files.append(sidecar.path)

    if moves:
        result["updated"] = _remap_paths(library_id, target_root, moves)
        result["moved"] = [
            {"from": old, "to": new, "is_dir": recursive}
            for old, new, recursive in moves
        ]
    removed, warnings = remove_empty_source_dirs(
        source, old_files, target_root, "", refresh_dir
    )
    result["removed_dirs"] = removed
    result["warnings"].extend(warnings)
    return result


def relocate_organized_items(
    library: dict[str, Any],
    source: FileSource,
    target: dict[str, Any],
    refresh_dir: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """把该作品已整理的文件移动到新位置；按作品文件夹整体移动，失败时退回逐集。"""
    library_id = int(library["id"])
    target_root = norm(library["target_root"] or "")
    key = target.get("match_key") or ""
    result: dict[str, Any] = {
        "moved": [],
        "skipped": [],
        "failed": [],
        "removed_dirs": [],
        "warnings": [],
        "updated": 0,
        "checked": 0,
    }
    if not target_root or not key:
        return result
    rows = query_all(
        "SELECT * FROM items WHERE library_id=? AND match_key=? AND status='organized' "
        "AND target_path IS NOT NULL AND target_path<>''",
        (library_id, key),
    )
    result["checked"] = len(rows)
    if not rows:
        return result

    organizer = Organizer(
        source,
        {**DEFAULT_NAMING, **(loads(library["naming"], {}) or {})},
        {**DEFAULT_SCRAPE, **(loads(library["scrape"], {}) or {})},
        manual_links=load_attachment_links(library_id),
        source_root=library["root_path"] or "",
        targets={key: target},
        refresh_dir=refresh_dir,
    )
    links = query_all(
        "SELECT video_path, attachment_path FROM attachment_links WHERE library_id=?",
        (library_id,),
    )
    attachment_by_video: dict[str, set[str]] = {}
    for link in links:
        attachment_by_video.setdefault(norm(link["video_path"]), set()).add(
            norm(link["attachment_path"])
        )

    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        parsed = loads(row["parsed"], {}) or {}
        match = loads(row["match"], {}) or {}
        if work_key(match) != key:
            continue
        current = _current_source_path(source, target_root, row["target_path"])
        if not current:
            result["skipped"].append(
                {"path": norm(row["target_path"]), "reason": "文件不在整理后目录内，未移动"}
            )
            continue
        if row.get("target_dir_override"):
            # 单个文件已经单独指定位置，整部作品迁移时不要把它挪走。
            result["skipped"].append(
                {"path": current, "reason": "已单独指定位置，未移动"}
            )
            continue
        if not _is_within(current, target_root):
            result["skipped"].append(
                {"path": current, "reason": "不在整理后目录内，未移动"}
            )
            continue
        new_folder, _ = organizer.target_for(
            parsed,
            match,
            source_path=current,
            target_root=target_root,
            target_name=row["target_name_override"] or "",
        )
        new_target = join(new_folder, posixpath.basename(current))
        if current == new_target:
            continue
        new_show_folder = organizer.show_folder_for(match, target_root)
        show_folder = _current_show_folder(organizer, parsed, match, current)
        group = groups.setdefault(
            show_folder,
            {"new_folders": set(), "rows": []},
        )
        group["new_folders"].add(new_show_folder)
        group["rows"].append(
            (row, current, new_target, new_show_folder, parsed, match)
        )

    moves: list[tuple[str, str, bool]] = []
    old_files: list[str] = []
    for show_folder, group in groups.items():
        if (
            show_folder
            and _is_within(show_folder, target_root)
            and norm(show_folder) != target_root
            and len(group["new_folders"]) == 1
        ):
            new_folder = next(iter(group["new_folders"]))
            if _is_within(new_folder, show_folder) or _is_within(show_folder, new_folder):
                # 目标就在旧文件夹内 / 外，交给逐集处理，避免把目录移进自身。
                pass
            else:
                sibling_rows = query_all(
                    "SELECT target_path, match_key FROM items WHERE library_id=? "
                    "AND target_path IS NOT NULL AND match_key IS NOT NULL AND match_key<>?",
                    (library_id, key),
                )
                others = [
                    row
                    for row in sibling_rows
                    if _is_within(norm(row["target_path"]), show_folder)
                ]
                if not others:
                    source.mkdir(new_folder)
                    before = len(moves)
                    _merge_move(
                        source,
                        show_folder,
                        new_folder,
                        moves,
                        result["skipped"],
                        result["failed"],
                    )
                    if len(moves) > before:
                        old_files.extend(move[0] for move in moves[before:])
                        continue
        for row, current, new_target, new_folder, parsed, match in group["rows"]:
            new_episode = parent_of(new_target)
            source.mkdir(new_episode)
            sidecars = _episode_sidecars(
                source,
                parent_of(current),
                current,
                attachment_by_video.get(current, set()),
            )
            _move_one_file(
                source,
                current,
                new_target,
                moves,
                result["skipped"],
                result["failed"],
            )
            if source.exists(current):
                # 视频没有移动成功时不要把字幕 / NFO 单独搬走，避免留下散落附件。
                continue
            old_files.append(current)
            for sidecar in sidecars:
                _move_one_file(
                    source,
                    sidecar.path,
                    join(new_episode, sidecar.name),
                    moves,
                    result["skipped"],
                    result["failed"],
                )
                if not source.exists(sidecar.path):
                    old_files.append(sidecar.path)
            # 移动完这一集的视频后，同一目录的字幕 / NFO 仍可能按旧名躺在原处；
            # 下一轮循环再处理其他集。

    if moves:
        result["updated"] = _remap_paths(library_id, target_root, moves)
        result["moved"] = [
            {"from": old, "to": new, "is_dir": recursive}
            for old, new, recursive in moves
        ]
    if old_files:
        removed, warnings = remove_empty_source_dirs(
            source, old_files, target_root, "", refresh_dir
        )
        result["removed_dirs"] = removed
        result["warnings"].extend(warnings)
    return result
