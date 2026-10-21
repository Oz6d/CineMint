"""海报墙真实性校验的短 TTL 目录列表缓存。

海报墙只显示待整理目录里仍真实存在的文件。WebDAV / OpenList 上每次
打开都重新列目录会很慢，这里按「影视库 + 父目录」缓存一小段时间。

- 只缓存目录列表，不缓存 items / works 结果；条目状态变化仍然实时生效。
- 应用内整理、扫描、重命名、删除、移动、修改影视库时主动失效。
- 外部客户端改名 / 删除无法感知，最多等待 TTL 过期。
- 目录暂时不可访问时按更短 TTL 缓存失败结果，避免反复打爆网盘。
"""

from __future__ import annotations

import threading
import time
from typing import Callable

TTL_SECONDS = 20.0
FAILURE_TTL_SECONDS = 5.0
MAX_ENTRIES = 2000

_lock = threading.Lock()
_cache: dict[tuple[int, str], tuple[float, set[str] | None]] = {}


def _prune(now: float) -> None:
    if len(_cache) <= MAX_ENTRIES:
        return
    for key, (expires_at, _) in list(_cache.items()):
        if expires_at <= now:
            _cache.pop(key, None)


def get_listing(
    library_id: int,
    path: str,
    loader: Callable[[], set[str]],
    *,
    ttl: float = TTL_SECONDS,
) -> set[str] | None:
    """返回缓存的目录文件名列表；失败时返回 None（不缓存异常本身）。"""
    key = (library_id, path)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    try:
        names = loader()
        expires_at = now + ttl
    except Exception:
        names = None
        expires_at = now + FAILURE_TTL_SECONDS

    with _lock:
        _prune(now)
        _cache[key] = (expires_at, names)
    return names


def invalidate_library(library_id: int) -> None:
    """影视库内文件发生变化后清掉该库的校验缓存。"""
    with _lock:
        for key in [key for key in _cache if key[0] == library_id]:
            _cache.pop(key, None)


def clear_presence_cache() -> None:
    with _lock:
        _cache.clear()
