"""Emby 媒体库刷新：整理完成、strm 同步完成后触发一次扫描。

配置在「设置 → Emby 入库」：服务器地址 + API Key，默认关闭。
同一进程内 60 秒内只触发一次，避免一个整理批次里多次请求。
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from ..db import get_setting, query_one
from ..security import decrypt

logger = logging.getLogger("cinemint.emby")

REFRESH_COOLDOWN = 60.0
TIMEOUT = 12.0

_last_refresh = 0.0
_lock = threading.Lock()


class EmbyError(RuntimeError):
    pass


def _config() -> tuple[str, str]:
    url = str(get_setting("emby_url", "") or "").strip().rstrip("/")
    key = decrypt(get_setting("emby_api_key", "") or "")
    return url, key


def enabled() -> bool:
    return bool(get_setting("emby_enabled", False))


def _request(method: str, path: str, url: str = "", key: str = "") -> httpx.Response:
    if not url or not key:
        url, key = _config()
    if not url or not key:
        raise EmbyError("请先配置 Emby 服务器地址和 API Key")
    headers = {"X-Emby-Token": key}
    try:
        response = httpx.request(
            method, f"{url}{path}", headers=headers, timeout=TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise EmbyError(f"无法连接 Emby：{exc}") from exc
    if response.status_code >= 400:
        raise EmbyError(f"Emby 返回 HTTP {response.status_code}")
    return response


def test_connection(url: str = "", key: str = "") -> dict:
    response = _request("GET", "/System/Info", url=url, key=key)
    try:
        data = response.json()
    except ValueError:
        data = {}
    return {
        "ok": True,
        "server": data.get("ServerName") or data.get("ProductName") or "",
        "version": data.get("Version") or "",
    }


def refresh_library(reason: str = "") -> bool:
    """触发 Emby 全库扫描；未启用 / 未配置 / 冷却中返回 False。"""
    if not enabled():
        return False
    url, key = _config()
    if not url or not key:
        logger.warning("Emby 自动刷新已开启，但缺少服务器地址或 API Key")
        return False
    global _last_refresh
    with _lock:
        now = time.monotonic()
        if now - _last_refresh < REFRESH_COOLDOWN:
            return False
        _last_refresh = now
    try:
        _request("POST", "/Library/Refresh", url=url, key=key)
    except EmbyError as exc:
        logger.warning("触发 Emby 扫描失败：%s", exc)
        return False
    logger.info("已触发 Emby 扫描媒体库（%s）", reason or "自动")
    return True


def refresh_now() -> None:
    """手动触发一次扫描（不受开关和冷却时间限制）。"""
    _request("POST", "/Library/Refresh")


def refresh_after_organize(library_id: int) -> bool:
    """整理结束：没有开启 strm 同步时立即刷新；开启时交给 strm 队列完成后再刷。"""
    if not enabled():
        return False
    library = query_one(
        "SELECT strm_sync_enabled FROM libraries WHERE id=?", (library_id,)
    )
    if library and int(library["strm_sync_enabled"] or 0):
        pending = query_one(
            "SELECT COUNT(*) AS c FROM strm_sync_queue "
            "WHERE library_id=? AND status='pending'",
            (library_id,),
        )
        if pending and int(pending["c"] or 0):
            return False
    return refresh_library("整理完成")


def refresh_after_strm(library_id: int) -> bool:
    """strm 队列没有待处理记录时，刷新一次 Emby。"""
    if not enabled():
        return False
    pending = query_one(
        "SELECT COUNT(*) AS c FROM strm_sync_queue "
        "WHERE library_id=? AND status='pending'",
        (library_id,),
    )
    if pending and int(pending["c"] or 0):
        return False
    return refresh_library("strm 同步完成")
