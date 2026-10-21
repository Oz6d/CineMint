from __future__ import annotations

import posixpath
from typing import Any, Iterable

from .presence_cache import get_listing
from .providers import FileSource, parent_of


def filter_existing(
    rows: Iterable[dict[str, Any]],
    source: FileSource,
    *,
    library_id: int | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """按真实目录过滤条目，返回 (存在的条目, 已不存在数量, 无法验证数量)。

    同一个父目录只请求一次；目录暂时无法访问时保守跳过，交给界面提示，
    避免把网络故障误判成文件已删除。

    传入 library_id 时使用短 TTL 目录缓存（海报墙）；不传时保持实时读取。
    """

    listings: dict[str, set[str] | None] = {}
    present: list[dict[str, Any]] = []
    hidden = 0
    unverified = 0
    for row in rows:
        source_path = row["source_path"]
        parent = parent_of(source_path)
        if parent not in listings:
            def load_names(parent: str = parent) -> set[str]:
                return {
                    entry.name for entry in source.list(parent) if not entry.is_dir
                }

            if library_id is None:
                try:
                    listings[parent] = load_names()
                except Exception:
                    listings[parent] = None
            else:
                listings[parent] = get_listing(library_id, parent, load_names)
        names = listings[parent]
        if names is None:
            unverified += 1
            continue
        if posixpath.basename(source_path) not in names:
            hidden += 1
            continue
        present.append(row)
    return present, hidden, unverified
