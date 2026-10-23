from __future__ import annotations

import posixpath
from typing import Any, Iterable

from .presence_cache import get_listing
from .providers import FileSource, SourceNotFound, parent_of


def filter_existing_rows(
    rows: Iterable[dict[str, Any]],
    source: FileSource,
    *,
    library_id: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """按真实目录过滤条目，返回 (存在的条目, 已不存在的条目, 无法验证的条目)。

    同一个父目录只请求一次。目录明确不存在（SourceNotFound，例如网盘里已经
    删除的目录）时按文件已丢失处理；网络超时 / 认证失败等临时错误才归为
    「无法验证」，避免把网络故障误判成文件已删除。

    传入 library_id 时使用短 TTL 目录缓存（海报墙）；不传时保持实时读取。
    """

    listings: dict[str, set[str] | None] = {}
    present: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for row in rows:
        source_path = row["source_path"]
        parent = parent_of(source_path)
        if parent not in listings:
            def load_names(parent: str = parent) -> set[str]:
                try:
                    return {
                        entry.name for entry in source.list(parent) if not entry.is_dir
                    }
                except SourceNotFound:
                    # 目录已不存在：其下的文件同样视为已丢失。
                    return set()

            if library_id is None:
                try:
                    listings[parent] = load_names()
                except Exception:
                    listings[parent] = None
            else:
                listings[parent] = get_listing(library_id, parent, load_names)
        names = listings[parent]
        if names is None:
            unverified.append(row)
            continue
        if posixpath.basename(source_path) not in names:
            hidden.append(row)
            continue
        present.append(row)
    return present, hidden, unverified


def filter_existing(
    rows: Iterable[dict[str, Any]],
    source: FileSource,
    *,
    library_id: int | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """按真实目录过滤条目，返回 (存在的条目, 已不存在数量, 无法验证数量)。"""

    present, hidden, unverified = filter_existing_rows(rows, source, library_id=library_id)
    return present, len(hidden), len(unverified)
