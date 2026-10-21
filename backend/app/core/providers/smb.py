from __future__ import annotations

import os
import posixpath
from typing import Any

from .base import Entry, FileSource, SourceError, norm, safe_relative


class SmbFileSource(FileSource):
    """通过 smbprotocol 的 smbclient 直接访问 SMB 共享，无需系统挂载。"""

    type = "smb"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.host = (self.config.get("host") or "").strip()
        self.share = (self.config.get("share") or "").strip().strip("/")
        self.username = self.config.get("username") or ""
        self.password = self.config.get("password") or ""
        self.port = int(self.config.get("port") or 445)
        self.domain = (self.config.get("domain") or "").strip()
        if not self.host or not self.share:
            raise SourceError("缺少 SMB 主机或共享名")
        import smbclient

        self.smbclient = smbclient
        user = f"{self.domain}\\{self.username}" if self.domain and self.username else self.username
        try:
            smbclient.register_session(
                self.host,
                username=user or None,
                password=self.password or None,
                port=self.port,
            )
        except Exception as exc:
            raise SourceError(f"SMB 登录失败: {exc}") from exc

    def _unc(self, path: str) -> str:
        rel = safe_relative(path)
        base = f"\\\\{self.host}\\{self.share}"
        return base + ("\\" + rel.replace("/", "\\") if rel else "")

    def list(self, path: str = "") -> list[Entry]:
        unc = self._unc(path)
        entries: list[Entry] = []
        try:
            for dirent in self.smbclient.scandir(unc):
                try:
                    stat = dirent.stat()
                    size, mtime = stat.st_size, stat.st_mtime
                except OSError:
                    size, mtime = 0, 0.0
                rel = norm(f"{norm(path)}/{dirent.name}")
                entries.append(
                    Entry(
                        name=dirent.name,
                        path=rel,
                        is_dir=dirent.is_dir(),
                        size=size,
                        mtime=mtime,
                    )
                )
        except Exception as exc:
            raise SourceError(f"SMB 读取失败 {unc}: {exc}") from exc
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.lower()))

    def exists(self, path: str) -> bool:
        try:
            self.smbclient.stat(self._unc(path))
            return True
        except Exception:
            return False

    def stat(self, path: str) -> Entry | None:
        try:
            stat = self.smbclient.stat(self._unc(path))
        except Exception:
            return None
        return Entry(
            name=norm(path).rsplit("/", 1)[-1],
            path=norm(path),
            is_dir=bool(stat.st_file_attributes & 0x10) if stat.st_file_attributes else False,
            size=stat.st_size,
            mtime=stat.st_mtime,
        )

    def mkdir(self, path: str) -> None:
        rel = safe_relative(path)
        if not rel:
            return
        try:
            self.smbclient.makedirs(self._unc(rel), exist_ok=True)
        except Exception as exc:
            raise SourceError(f"SMB 创建目录失败 {rel}: {exc}") from exc

    def read(self, path: str) -> bytes:
        try:
            with self.smbclient.open_file(self._unc(path), mode="rb") as handle:
                return handle.read()
        except Exception as exc:
            raise SourceError(f"SMB 读取失败 {path}: {exc}") from exc

    def write(self, path: str, data: bytes) -> None:
        parent = posixpath.dirname(norm(path))
        if parent:
            self.mkdir(parent)
        try:
            with self.smbclient.open_file(self._unc(path), mode="wb") as handle:
                handle.write(data)
        except Exception as exc:
            raise SourceError(f"SMB 写入失败 {path}: {exc}") from exc

    def move(self, src: str, dst: str) -> None:
        source, target = safe_relative(src), safe_relative(dst)
        if source == target:
            return
        parent = posixpath.dirname(target)
        if parent:
            self.mkdir(parent)
        try:
            self.smbclient.rename(self._unc(source), self._unc(target))
        except Exception as exc:
            raise SourceError(f"SMB 移动失败 {source}: {exc}") from exc

    def delete(self, path: str) -> None:
        unc = self._unc(path)
        try:
            stat = self.smbclient.stat(unc)
            if stat.st_file_attributes & 0x10:
                self.smbclient.rmdir(unc)
            else:
                self.smbclient.remove(unc)
        except Exception as exc:
            raise SourceError(f"SMB 删除失败 {path}: {exc}") from exc

    def test(self) -> dict[str, Any]:
        entries = self.list("")
        return {"ok": True, "entries": len(entries)}
