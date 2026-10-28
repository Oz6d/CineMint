"""高版本（V2 / REPACK）替换已整理的同一集。

安全策略：先把旧集的视频、字幕、音轨、单集 NFO 移到目标目录下的
隐藏回收目录，整理成功后删除；整理失败则尽量原路恢复。
"""

from __future__ import annotations

import posixpath
from typing import Any

from ..db import execute, query_all
from .parser import SIDECAR_EXTS, file_ext
from .providers import SourceError, join, norm, parent_of

AUDIO_SIDECAR_EXTS = {".mka", ".flac", ".aac", ".ac3", ".eac3", ".dts", ".mp3", ".m4a", ".opus"}
EPISODE_SIDECAR_EXTS = set(SIDECAR_EXTS) | AUDIO_SIDECAR_EXTS
TRASH_DIR = ".cinemint-trash"


def _splitext(name: str) -> tuple[str, str]:
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    return name[:dot], name[dot:]


def episode_files(source: Any, target_path: str) -> list[str]:
    """返回旧集视频及其单集附带文件（不含海报、tvshow.nfo 等文件夹级资源）。"""
    if not target_path:
        return []
    target = norm(target_path)
    parent = parent_of(target)
    stem, _ = _splitext(posixpath.basename(target))
    files: list[str] = []
    try:
        entries = source.list(parent)
    except SourceError:
        entries = []
    for entry in entries:
        if entry.is_dir:
            continue
        path = norm(entry.path)
        if path == target:
            files.append(path)
            continue
        if not entry.name.startswith(stem + "."):
            continue
        if file_ext(entry.name) in EPISODE_SIDECAR_EXTS:
            files.append(path)
    if not files:
        try:
            if source.exists(target):
                files.append(target)
        except SourceError:
            pass
    return list(dict.fromkeys(files))


class EpisodeReplacement:
    def __init__(
        self,
        library: dict[str, Any],
        source: Any,
        old_item: dict[str, Any],
        *,
        run_id: int,
    ) -> None:
        self.library = library
        self.source = source
        self.old_item = old_item
        self.run_id = run_id
        self.moved: list[tuple[str, str]] = []
        self.warnings: list[str] = []
        self.committed = False
        self.rolled_back = False
        self.backup_dir = ""

    def __enter__(self) -> "EpisodeReplacement":
        old_target = norm(self.old_item.get("target_path") or "")
        if not old_target:
            raise SourceError("旧条目没有目标路径，无法替换")
        files = episode_files(self.source, old_target)
        if not files:
            raise SourceError(f"旧版本文件已不存在: {old_target}")
        target_root = norm(self.library.get("target_root") or "") or parent_of(old_target)
        base = join(target_root, TRASH_DIR, f"replace-{self.run_id}-{self.old_item['id']}")
        self.backup_dir = base
        self.source.mkdir(base)
        try:
            for path in files:
                name = posixpath.basename(path)
                destination = join(base, name)
                if self.source.exists(destination):
                    stem, ext = _splitext(name)
                    destination = join(base, f"{stem}-{len(self.moved) + 1}{ext}")
                self.source.move(path, destination)
                self.moved.append((path, destination))
        except Exception:
            self.rollback()
            raise
        return self

    def _cleanup_links(self) -> None:
        old_paths = {norm(path) for path, _ in self.moved}
        rows = query_all(
            "SELECT id, video_path, attachment_path FROM attachment_links WHERE library_id=?",
            (self.library["id"],),
        )
        for row in rows:
            if norm(row["video_path"]) in old_paths or norm(row["attachment_path"]) in old_paths:
                execute("DELETE FROM attachment_links WHERE id=?", (row["id"],))

    def commit(self) -> None:
        if self.committed:
            return
        for _, backup in self.moved:
            try:
                self.source.delete(backup)
            except SourceError as exc:
                self.warnings.append(f"旧版本文件删除失败: {backup} ({exc})")
        self._cleanup_links()
        try:
            if self.backup_dir and not self.source.list(self.backup_dir):
                self.source.remove_empty_dir(self.backup_dir)
                trash = parent_of(self.backup_dir)
                if trash and not self.source.list(trash):
                    self.source.remove_empty_dir(trash)
        except SourceError:
            pass
        self.committed = True

    def rollback(self) -> None:
        if self.committed or self.rolled_back:
            return
        self.rolled_back = True
        for original, backup in reversed(self.moved):
            if self.source.exists(original):
                self.warnings.append(
                    f"旧文件恢复位置已被占用，保留备份：{backup}"
                )
                continue
            try:
                self.source.move(backup, original)
            except SourceError as exc:
                self.warnings.append(f"旧文件恢复失败: {backup} ({exc})")
        if self.backup_dir:
            try:
                if not self.source.list(self.backup_dir):
                    self.source.remove_empty_dir(self.backup_dir)
                    trash = parent_of(self.backup_dir)
                    if trash and not self.source.list(trash):
                        self.source.remove_empty_dir(trash)
            except SourceError:
                pass

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.committed and not self.rolled_back:
            self.rollback()
