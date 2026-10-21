from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .base import Entry, FileSource, SourceError, norm, safe_relative


class LocalFileSource(FileSource):
    type = "local"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        base = self.config.get("base_path") or "/media"
        self.base = Path(base).expanduser().resolve()

    def _raw_abs(self, path: str) -> Path:
        """返回不解析最后一级符号链接的路径，用来识别 / 删除链接本身。"""
        rel = safe_relative(path)
        if not rel:
            return self.base
        raw = self.base / rel
        parent = raw.parent.resolve()
        if parent != self.base and self.base not in parent.parents:
            raise SourceError("路径越界")
        return raw

    def _abs(self, path: str) -> Path:
        rel = safe_relative(path)
        target = (self.base / rel).resolve()
        if target != self.base and self.base not in target.parents:
            raise SourceError("路径越界")
        return target

    def list(self, path: str = "") -> list[Entry]:
        root = self._abs(path)
        if not root.exists():
            raise SourceError(f"目录不存在: {root}")
        items: list[Entry] = []
        try:
            with os.scandir(root) as it:
                for dirent in it:
                    try:
                        stat = dirent.stat() if dirent.is_dir(follow_symlinks=False) else dirent.stat()
                    except OSError:
                        stat = None
                    rel = norm(f"{norm(path)}/{dirent.name}")
                    items.append(
                        Entry(
                            name=dirent.name,
                            path=rel,
                            is_dir=dirent.is_dir(follow_symlinks=True),
                            size=stat.st_size if stat and stat.st_size > 0 else 0,
                            mtime=stat.st_mtime if stat else 0.0,
                            is_symlink=dirent.is_symlink(),
                        )
                    )
        except PermissionError as exc:
            raise SourceError(f"没有访问权限: {root}") from exc
        return sorted(items, key=lambda entry: (not entry.is_dir, entry.name.lower()))

    def exists(self, path: str) -> bool:
        target = self._raw_abs(path)
        return target.exists() or target.is_symlink()

    def stat(self, path: str) -> Entry | None:
        target = self._raw_abs(path)
        if not target.exists() and not target.is_symlink():
            return None
        try:
            stat = target.stat()
            size, mtime = stat.st_size, stat.st_mtime
        except OSError:
            size, mtime = 0, 0.0
        return Entry(
            name=target.name,
            path=norm(path),
            is_dir=target.is_dir(),
            size=size,
            mtime=mtime,
            is_symlink=target.is_symlink(),
        )

    def mkdir(self, path: str) -> None:
        self._abs(path).mkdir(parents=True, exist_ok=True)

    def read(self, path: str) -> bytes:
        return self._abs(path).read_bytes()

    def write(self, path: str, data: bytes) -> None:
        target = self._abs(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".cineflow.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)

    def move(self, src: str, dst: str) -> None:
        source, target = self._raw_abs(src), self._raw_abs(dst)
        if source == target:
            return
        if not source.exists():
            raise SourceError(f"源文件不存在: {src}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, target)
        except OSError:
            shutil.move(str(source), str(target))

    def delete(self, path: str) -> None:
        target = self._raw_abs(path)
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
