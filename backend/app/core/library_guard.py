"""按影视库串行化文件操作。

任务中心与订阅执行器都必须在扫描/整理/重命名期间持有对应库的锁，
避免两个线程同时移动同一批文件。锁只在进程内有效，重启后自动释放。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class LibraryBusy(RuntimeError):
    pass


_state = threading.Lock()
_locks: dict[int, threading.Lock] = {}


def _lock_for(library_id: int) -> threading.Lock:
    with _state:
        lock = _locks.get(library_id)
        if lock is None:
            lock = threading.Lock()
            _locks[library_id] = lock
        return lock


@contextmanager
def hold_library(library_id: int, timeout: float = 300.0) -> Iterator[None]:
    lock = _lock_for(library_id)
    if not lock.acquire(timeout=timeout):
        raise LibraryBusy(f"影视库 {library_id} 正在被其他任务使用")
    try:
        yield
    finally:
        lock.release()
