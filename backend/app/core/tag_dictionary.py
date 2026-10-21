"""用户可扩展的附加信息词库。

「附加信息表」里分辨率 / 视频质量 / 来源 / 视频编码 / 音频信息属于行业固定
标准，不开放修改；影视版本 / 发布标签 / 发布组允许追加自定义词条。

自定义词条保存在 settings 表的 custom_tag_dictionary 键中。解析器只读取本模块
的内存缓存，避免每次解析文件名都查询数据库。
"""

from __future__ import annotations

import re
import threading
import unicodedata
from typing import Any, Iterable

FIELDS = ("edition", "release_notes", "group")
MAX_ENTRIES = 300
MAX_LENGTH = 64

_lock = threading.RLock()
_custom: dict[str, list[str]] = {field: [] for field in FIELDS}
_custom_keys: dict[str, set[str]] = {field: set() for field in FIELDS}
_patterns: dict[str, re.Pattern[str] | None] = {field: None for field in FIELDS}


def _key(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _clean(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_LENGTH].strip()


def _normalize(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean(value)
        if not text or not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", text):
            continue
        key = _key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= MAX_ENTRIES:
            break
    return result


def _phrase(value: str) -> str:
    """把自定义词条转成允许 . _ - 空格互相替代的正则片段。"""
    parts = [re.escape(part) for part in re.split(r"[\s._-]+", value) if part]
    if not parts:
        return ""
    return r"[ ._-]+".join(parts)


def _build_pattern(field: str) -> re.Pattern[str] | None:
    alternatives = {
        _phrase(unicodedata.normalize("NFKC", value))
        for value in _custom.get(field, [])
        if _phrase(unicodedata.normalize("NFKC", value))
    }
    if not alternatives:
        return None
    # 长词优先，避免 REAL.PROPER 先被 REAL 吃掉
    ordered = sorted(alternatives, key=len, reverse=True)
    return re.compile(
        rf"(?i)(?<![a-z0-9])(?:{'|'.join(ordered)})(?![a-z0-9])"
    )


def normalize_tag_dictionary(values: dict[str, Any] | None) -> dict[str, list[str]]:
    data = values if isinstance(values, dict) else {}
    result: dict[str, list[str]] = {}
    for field in FIELDS:
        raw = data.get(field)
        if isinstance(raw, str):
            raw = [raw]
        elif not isinstance(raw, (list, tuple, set)):
            raw = []
        result[field] = _normalize(raw)
    return result


def set_custom_tags(values: dict[str, Any] | None) -> dict[str, list[str]]:
    """更新内存缓存，返回清理后的词库。"""
    cleaned = normalize_tag_dictionary(values)
    with _lock:
        for field in FIELDS:
            _custom[field] = cleaned[field]
            _custom_keys[field] = {_key(value) for value in cleaned[field]}
            _patterns[field] = _build_pattern(field)
    return get_custom_tags()


def get_custom_tags() -> dict[str, list[str]]:
    with _lock:
        return {field: list(_custom[field]) for field in FIELDS}


def custom_field_pattern(field: str) -> re.Pattern[str] | None:
    with _lock:
        return _patterns.get(field)


def custom_fullmatch(field: str, text: str) -> bool:
    pattern = custom_field_pattern(field)
    return bool(pattern and pattern.fullmatch(text))


def is_custom_group(value: str) -> bool:
    key = _key(value)
    with _lock:
        return key in _custom_keys.get("group", set())


def load_custom_tags_from_db() -> dict[str, list[str]]:
    """启动时从 settings 表加载；数据库不可用时保持默认空词库。"""
    try:
        from ..db import get_setting

        raw = get_setting("custom_tag_dictionary", {})
    except Exception:
        return get_custom_tags()
    if isinstance(raw, dict):
        return set_custom_tags(raw)
    return get_custom_tags()
