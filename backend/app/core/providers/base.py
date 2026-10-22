from __future__ import annotations

import posixpath
from dataclasses import dataclass, asdict
from typing import Any


class SourceError(RuntimeError):
    pass


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    is_symlink: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def norm(path: str) -> str:
    path = (path or "").replace("\\", "/").strip()
    path = posixpath.normpath(path) if path else ""
    if path == ".":
        return ""
    return path.lstrip("/")


def parent_of(path: str) -> str:
    norm_path = norm(path)
    if not norm_path or "/" not in norm_path:
        return ""
    return norm_path.rsplit("/", 1)[0]


def join(*parts: str) -> str:
    clean = [norm(p) for p in parts]
    return norm("/".join(p for p in clean if p))


def safe_relative(path: str) -> str:
    normalized = norm(path)
    if normalized.startswith("..") or "/../" in f"/{normalized}/":
        raise SourceError("路径越界")
    return normalized


class FileSource:
    type = "base"

    def __init__(self, config: dict[str, Any]):
        self.config = config or {}

    def list(self, path: str = "") -> list[Entry]:
        raise NotImplementedError

    def list_dirs(self, path: str = "") -> list[Entry]:
        return sorted(
            (entry for entry in self.list(path) if entry.is_dir),
            key=lambda entry: entry.name.lower(),
        )

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def stat(self, path: str) -> Entry | None:
        raise NotImplementedError

    def mkdir(self, path: str) -> None:
        raise NotImplementedError

    def read(self, path: str) -> bytes:
        raise NotImplementedError

    def write(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    def move(self, src: str, dst: str) -> None:
        raise NotImplementedError

    def delete(self, path: str) -> None:
        raise NotImplementedError

    def remove_empty_dir(self, path: str) -> None:
        """只删除空目录；非空目录必须拒绝，避免误删。"""
        if self.list(path):
            raise SourceError(f"目录不为空: {path}")
        self.delete(path)

    def test(self) -> dict[str, Any]:
        entries = self.list("")
        return {"ok": True, "entries": len(entries)}
