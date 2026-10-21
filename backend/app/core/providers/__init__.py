from __future__ import annotations

from typing import Any

from ...security import decrypt
from .base import Entry, FileSource, SourceError, join, norm, parent_of, safe_relative
from .local import LocalFileSource
from .smb import SmbFileSource
from .webdav import WebDavFileSource

SECRET_KEYS = {"password", "api_key", "token", "secret"}


def decrypt_config(config: dict[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(config, str):
        try:
            import json

            config = json.loads(config)
        except ValueError:
            config = {}
    result: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if isinstance(value, str) and key in SECRET_KEYS:
            result[key] = decrypt(value)
        else:
            result[key] = value
    return result


def build_source(source: dict[str, Any]) -> FileSource:
    config = decrypt_config(source.get("config"))
    source_type = source.get("type")
    if source_type == "local":
        return LocalFileSource(config)
    if source_type == "webdav":
        return WebDavFileSource(config)
    if source_type == "smb":
        return SmbFileSource(config)
    raise SourceError(f"未知的文件源类型: {source_type}")


__all__ = [
    "Entry",
    "FileSource",
    "SourceError",
    "build_source",
    "decrypt_config",
    "join",
    "norm",
    "parent_of",
    "safe_relative",
]
