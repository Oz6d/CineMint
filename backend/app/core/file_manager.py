"""待整理目录的文件浏览、批量删除与移动。

「全部文件」视图直接读取文件源，不依赖 items 表；删除 / 移动后会同步
items 与 attachment_links，避免留下幽灵记录。所有路径都相对影视库根目录，
并限制在根目录内。
"""

from __future__ import annotations

import posixpath
import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from ..db import db, now
from .attachments import ATTACHMENT_IMAGE_EXTS, AUDIO_EXTS, META_EXTS, SUBTITLE_EXTS
from .parser import VIDEO_EXTS, file_ext
from .providers.base import Entry, FileSource, SourceError, join, norm, safe_relative

MAX_MATCHES = 5000
MAX_SCAN = 50000
MAX_DEPTH = 32

# 递归清理时跳过明显的回收站 / 隐藏目录，避免误删系统目录。
SKIP_CLEANUP_DIRS = {
    "@eadir",
    "#recycle",
    "$recycle.bin",
    "system volume information",
    ".trash",
    ".@__thumb",
    "lost+found",
    "node_modules",
    ".git",
    "filmix.trash",
    "recycle bin",
    "recycler",
}


def library_path(root: str, path: str) -> str:
    """把影视库内的相对路径转成文件源内的完整路径。"""
    return join(root, safe_relative(path))


def relative_path(root: str, path: str) -> str:
    """把文件源内的完整路径转成相对影视库根目录的路径。"""
    root = norm(root)
    path = norm(path)
    if not root:
        return path
    if path == root:
        return ""
    if path.startswith(root + "/"):
        return path[len(root) + 1:]
    raise SourceError("路径不在影视库内")


def _path_under(path: str, prefix: str) -> bool:
    path, prefix = norm(path), norm(prefix)
    if not prefix:
        return False
    return path == prefix or path.startswith(prefix + "/")


def _replace_prefix(path: str, old_prefix: str, new_prefix: str) -> str:
    path, old_prefix, new_prefix = norm(path), norm(old_prefix), norm(new_prefix)
    if path == old_prefix:
        return new_prefix
    if path.startswith(old_prefix + "/"):
        return new_prefix + path[len(old_prefix):]
    return path


def _mtime_iso(mtime: float) -> str | None:
    if not mtime:
        return None
    try:
        return datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _kind(name: str) -> str:
    ext = file_ext(name)
    if ext in VIDEO_EXTS:
        return "video"
    if ext in SUBTITLE_EXTS:
        return "subtitle"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in ATTACHMENT_IMAGE_EXTS:
        return "image"
    if ext in META_EXTS:
        return "meta"
    return "other"


def serialize_entry(entry: Entry, root: str) -> dict[str, Any]:
    return {
        "name": entry.name,
        "path": relative_path(root, entry.path),
        "is_dir": entry.is_dir,
        "size": entry.size,
        "mtime": entry.mtime,
        "modified_at": _mtime_iso(entry.mtime),
        "kind": "folder" if entry.is_dir else _kind(entry.name),
        "extension": file_ext(entry.name),
        "is_symlink": entry.is_symlink,
    }


def list_directory(source: FileSource, root: str, path: str = "") -> dict[str, Any]:
    rel = safe_relative(path)
    full = library_path(root, rel)
    if rel:
        info = source.stat(full)
        if not info or not info.is_dir:
            raise SourceError(f"目录不存在: {rel}")
    entries = source.list(full)
    serialized = [serialize_entry(entry, root) for entry in entries]
    files = [entry for entry in serialized if not entry["is_dir"]]
    dirs = [entry for entry in serialized if entry["is_dir"]]
    return {
        "root_path": norm(root),
        "path": rel,
        "parent": rel.rpartition("/")[0] if rel and "/" in rel else ("" if rel else None),
        "entries": serialized,
        "counts": {
            "files": len(files),
            "dirs": len(dirs),
            "size": sum(entry["size"] for entry in files),
        },
    }


def _skip_cleanup_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered in SKIP_CLEANUP_DIRS or lowered.endswith(".trash") or name.startswith(".")


def _find_files(
    source: FileSource,
    root: str,
    path: str,
    *,
    recursive: bool,
    predicate: Callable[[str], bool],
) -> tuple[list[Entry], int, bool, list[str]]:
    rel = safe_relative(path)
    start = library_path(root, rel)
    if rel:
        info = source.stat(start)
        if not info or not info.is_dir:
            raise SourceError(f"目录不存在: {rel}")
    matches: list[Entry] = []
    warnings: list[str] = []
    scanned = 0
    truncated = False
    stack: list[tuple[str, int]] = [(start, 0)]
    first = True

    while stack:
        current, depth = stack.pop()
        try:
            entries = source.list(current)
        except SourceError as exc:
            if first:
                raise
            try:
                label = relative_path(root, current)
            except SourceError:
                label = current
            warnings.append(f"{label or '/'}: {exc}")
            continue
        first = False

        for entry in entries:
            scanned += 1
            if scanned > MAX_SCAN:
                return sorted(matches, key=lambda item: item.path), scanned - 1, True, warnings
            if entry.is_dir:
                if recursive and not entry.is_symlink and depth < MAX_DEPTH and not _skip_cleanup_dir(entry.name):
                    stack.append((entry.path, depth + 1))
                continue
            if predicate(entry.name):
                matches.append(entry)
                if len(matches) >= MAX_MATCHES:
                    return sorted(matches, key=lambda item: item.path), scanned, True, warnings

    return sorted(matches, key=lambda item: item.path), scanned, truncated, warnings


def match_files(
    source: FileSource,
    root: str,
    *,
    path: str = "",
    pattern: str,
    mode: str = "contains",
    recursive: bool = False,
) -> dict[str, Any]:
    pattern = (pattern or "").strip()
    if not pattern:
        raise SourceError("请输入要匹配的内容")
    if len(pattern) > 200:
        raise SourceError("匹配内容不能超过 200 个字符")
    if mode not in {"contains", "regex"}:
        raise SourceError("匹配方式不支持")
    try:
        regex = re.compile(pattern if mode == "regex" else re.escape(pattern), re.IGNORECASE)
    except re.error as exc:
        raise SourceError(f"正则表达式不合法: {exc}") from exc

    matches, scanned, truncated, warnings = _find_files(
        source,
        root,
        path,
        recursive=recursive,
        predicate=lambda name: bool(regex.search(name)),
    )
    return {
        "path": safe_relative(path),
        "pattern": pattern,
        "mode": mode,
        "recursive": recursive,
        "matches": [serialize_entry(entry, root) for entry in matches],
        "total": len(matches),
        "scanned": scanned,
        "truncated": truncated,
        "warnings": warnings,
    }


def _compact_paths(paths: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for path in sorted({safe_relative(path) for path in paths if path is not None}):
        if not path:
            raise SourceError("不能操作影视库根目录")
        if any(path == parent or path.startswith(parent + "/") for parent in cleaned):
            continue
        cleaned.append(path)
    if not cleaned:
        raise SourceError("请选择要操作的文件")
    return cleaned


def _delete_db_records(library_id: int, path: str, *, recursive: bool) -> tuple[int, int]:
    """删除文件 / 文件夹后清理对应的 items 与手动关联。"""
    removed_items = 0
    removed_links = 0
    with db() as conn:
        items = conn.execute(
            "SELECT id, source_path FROM items WHERE library_id=?",
            (library_id,),
        ).fetchall()
        item_ids = [
            row["id"]
            for row in items
            if (recursive and _path_under(row["source_path"], path))
            or (not recursive and norm(row["source_path"]) == norm(path))
        ]
        if item_ids:
            conn.executemany("DELETE FROM items WHERE id=?", [(item_id,) for item_id in item_ids])
            removed_items = len(item_ids)

        links = conn.execute(
            "SELECT id, video_path, attachment_path FROM attachment_links WHERE library_id=?",
            (library_id,),
        ).fetchall()
        link_ids = []
        for row in links:
            video = row["video_path"]
            attachment = row["attachment_path"]
            if recursive:
                matched = _path_under(video, path) or _path_under(attachment, path)
            else:
                matched = norm(video) == norm(path) or norm(attachment) == norm(path)
            if matched:
                link_ids.append(row["id"])
        if link_ids:
            conn.executemany("DELETE FROM attachment_links WHERE id=?", [(link_id,) for link_id in link_ids])
            removed_links = len(link_ids)

    return removed_items, removed_links


def _remap_db_paths(
    library_id: int,
    old_path: str,
    new_path: str,
    *,
    recursive: bool,
) -> tuple[int, int]:
    """文件 / 文件夹移动后同步 items 和手动关联路径。"""
    updated_items = 0
    updated_links = 0
    old_path, new_path = norm(old_path), norm(new_path)
    with db() as conn:
        items = conn.execute(
            "SELECT id, source_path FROM items WHERE library_id=?",
            (library_id,),
        ).fetchall()
        for row in items:
            current = norm(row["source_path"])
            matched = _path_under(current, old_path) if recursive else current == old_path
            if not matched:
                continue
            updated = _replace_prefix(current, old_path, new_path)
            if updated == current:
                continue
            conn.execute(
                "UPDATE items SET source_path=?, file_name=?, updated_at=? WHERE id=?",
                (updated, posixpath.basename(updated), now(), row["id"]),
            )
            updated_items += 1

        links = conn.execute(
            "SELECT id, video_path, attachment_path FROM attachment_links WHERE library_id=?",
            (library_id,),
        ).fetchall()
        for row in links:
            video = norm(row["video_path"])
            attachment = norm(row["attachment_path"])
            video_matched = _path_under(video, old_path) if recursive else video == old_path
            attachment_matched = _path_under(attachment, old_path) if recursive else attachment == old_path
            if not video_matched and not attachment_matched:
                continue
            new_video = _replace_prefix(video, old_path, new_path) if video_matched else video
            new_attachment = (
                _replace_prefix(attachment, old_path, new_path)
                if attachment_matched
                else attachment
            )
            if new_video == video and new_attachment == attachment:
                continue
            # 目标路径如果已有旧的关联记录，先移除，避免 UNIQUE 冲突。
            conn.execute(
                "DELETE FROM attachment_links WHERE library_id=? AND attachment_path=? AND id<>?",
                (library_id, new_attachment, row["id"]),
            )
            conn.execute(
                "UPDATE attachment_links SET video_path=?, attachment_path=? WHERE id=?",
                (new_video, new_attachment, row["id"]),
            )
            updated_links += 1

    return updated_items, updated_links


def _delete_tree(source: FileSource, path: str, info: Entry, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise SourceError(f"目录层级过深，已停止删除: {path}")
    if info.is_dir and not info.is_symlink:
        for child in source.list(path):
            _delete_tree(source, child.path, child, depth + 1)
    source.delete(path)


def delete_paths(
    source: FileSource,
    library_id: int,
    root: str,
    paths: Iterable[str],
) -> dict[str, Any]:
    selected = _compact_paths(paths)
    result: dict[str, Any] = {
        "deleted": [],
        "failed": [],
        "items_removed": 0,
        "links_removed": 0,
    }
    for rel in selected:
        full = library_path(root, rel)
        try:
            info = source.stat(full)
            if not info:
                result["failed"].append({"path": rel, "error": "文件不存在"})
                continue
            _delete_tree(source, full, info)
            removed_items, removed_links = _delete_db_records(
                library_id,
                full,
                recursive=info.is_dir,
            )
            result["deleted"].append(
                {"path": rel, "name": info.name, "is_dir": info.is_dir, "size": info.size}
            )
            result["items_removed"] += removed_items
            result["links_removed"] += removed_links
        except SourceError as exc:
            result["failed"].append({"path": rel, "error": str(exc)})
    return result


def move_paths(
    source: FileSource,
    library_id: int,
    root: str,
    paths: Iterable[str],
    destination: str,
) -> dict[str, Any]:
    selected = _compact_paths(paths)
    dest_rel = safe_relative(destination)
    dest_full = library_path(root, dest_rel)

    for rel in selected:
        source_full = library_path(root, rel)
        if dest_full == source_full or dest_full.startswith(source_full + "/"):
            raise SourceError("不能把文件 / 文件夹移动到自身内部")

    # 先校验所有源文件与目标冲突，避免只移动一部分。
    plans: list[tuple[str, str, str, Entry]] = []
    conflicts: list[str] = []
    seen_targets: set[str] = set()
    for rel in selected:
        source_full = library_path(root, rel)
        info = source.stat(source_full)
        if not info:
            conflicts.append(f"{rel}（不存在）")
            continue
        target_full = join(dest_full, info.name)
        if target_full == source_full:
            continue
        if source.exists(target_full):
            conflicts.append(f"{rel} → {relative_path(root, target_full)}（目标已存在）")
            continue
        if target_full in seen_targets:
            conflicts.append(f"{rel}（多个文件同名）")
            continue
        seen_targets.add(target_full)
        plans.append((rel, source_full, target_full, info))

    if conflicts:
        raise SourceError("无法移动，目标目录已有同名文件：" + "；".join(conflicts[:5]))

    if dest_full:
        info = source.stat(dest_full)
        if info and not info.is_dir:
            raise SourceError("目标路径不是文件夹")
        if not info:
            source.mkdir(dest_full)

    result: dict[str, Any] = {
        "destination": dest_rel,
        "moved": [],
        "skipped": [],
        "failed": [],
        "items_updated": 0,
        "links_updated": 0,
    }
    for rel, source_full, target_full, info in plans:
        target_rel = relative_path(root, target_full)
        try:
            source.move(source_full, target_full)
            if source.exists(source_full):
                raise SourceError("移动后源文件仍然存在，服务器可能只完成了复制")
            updated_items, updated_links = _remap_db_paths(
                library_id,
                source_full,
                target_full,
                recursive=info.is_dir,
            )
            result["moved"].append(
                {
                    "from": rel,
                    "to": target_rel,
                    "name": info.name,
                    "is_dir": info.is_dir,
                    "size": info.size,
                }
            )
            result["items_updated"] += updated_items
            result["links_updated"] += updated_links
        except SourceError as exc:
            # 某些 WebDAV 服务在移动成功后仍返回错误；以实际文件状态为准。
            if not source.exists(source_full) and source.exists(target_full):
                updated_items, updated_links = _remap_db_paths(
                    library_id,
                    source_full,
                    target_full,
                    recursive=info.is_dir,
                )
                result["moved"].append(
                    {
                        "from": rel,
                        "to": target_rel,
                        "name": info.name,
                        "is_dir": info.is_dir,
                        "size": info.size,
                    }
                )
                result["items_updated"] += updated_items
                result["links_updated"] += updated_links
            else:
                result["failed"].append({"path": rel, "error": str(exc)})

    for rel in selected:
        if all(plan[0] != rel for plan in plans):
            result["skipped"].append(rel)
    return result


def _validate_rename_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise SourceError("请输入新的名称")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise SourceError("名称只能是文件名，不能包含路径分隔符")
    if any(ord(char) < 32 for char in name):
        raise SourceError("名称不能包含控制字符")
    if len(name) > 255:
        raise SourceError("名称不能超过 255 个字符")
    return name


def rename_path(
    source: FileSource,
    library_id: int,
    root: str,
    path: str,
    new_name: str,
) -> dict[str, Any]:
    """重命名待整理目录内的单个文件或文件夹，不改变其父目录。"""
    rel = safe_relative(path)
    if not rel:
        raise SourceError("不能重命名影视库根目录")
    new_name = _validate_rename_name(new_name)
    full = library_path(root, rel)
    info = source.stat(full)
    if not info:
        raise SourceError("文件 / 文件夹不存在")
    old_name = posixpath.basename(rel)
    parent = posixpath.dirname(rel)
    target_rel = posixpath.join(parent, new_name) if parent else new_name
    if target_rel == rel:
        return {
            "from": rel,
            "to": target_rel,
            "name": old_name,
            "new_name": new_name,
            "is_dir": info.is_dir,
            "size": info.size,
            "items_updated": 0,
            "links_updated": 0,
        }
    target_full = library_path(root, target_rel)
    if source.exists(target_full):
        raise SourceError(f"目标已存在: {target_rel}")
    try:
        source.move(full, target_full)
        if source.exists(full):
            raise SourceError("重命名后源文件仍然存在，服务器可能只完成了复制")
    except SourceError:
        # WebDAV 有时在操作成功后仍返回错误，以实际状态为准。
        if not source.exists(full) and source.exists(target_full):
            pass
        else:
            raise
    updated_items, updated_links = _remap_db_paths(
        library_id, full, target_full, recursive=info.is_dir
    )
    return {
        "from": rel,
        "to": target_rel,
        "name": old_name,
        "new_name": new_name,
        "is_dir": info.is_dir,
        "size": info.size,
        "items_updated": updated_items,
        "links_updated": updated_links,
    }
