"""AI 选目录：把整理后目录的现有分类快照交给模型，从真实目录里挑一个。

快照写在 data/cache/library_structure_<库ID>.json，只保存 1～2 层文件夹名，
不写进媒体库本身；模型只能选快照里已有的路径，保存前还会再校验一次。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import settings
from ..db import get_setting, now
from .metadata.ai import AIError, chat_json
from .organize_targets import normalize_target_path, save_target, work_key
from .providers import FileSource, SourceError, join, norm

STRUCTURE_TTL = 600.0
MAX_TOP_DIRS = 60
MAX_SUB_DIRS = 40
MAX_TOTAL_DIRS = 120

SUGGEST_PROMPT = """你是影视库归档助手。下面是整理后目录里已有的分类文件夹，以及一部作品的信息。
请为这部作品选择一个最合适的已有分类，规则：
- 只能从 paths 里选一个完全一致的路径，不要发明新目录。
- 按常见习惯判断：电影 / 剧集 / 动漫 / 特摄 / 纪录片 / 综艺，以及华语 / 日韩 / 欧美等地区。
- 明显都不合适或信息不足时，path 返回空字符串，并降低 confidence。
只输出 JSON：
{"path": "分类路径或空字符串", "confidence": 0.0 到 1.0, "reason": "一句话说明"}
"""


def _structure_file(library_id: int) -> Path:
    return settings.data_dir / "cache" / f"library_structure_{int(library_id)}.json"


def invalidate_structure(library_id: int) -> None:
    try:
        _structure_file(library_id).unlink(missing_ok=True)
    except OSError:
        pass


def _read_cache(library_id: int, target_root: str, max_age: float) -> dict[str, Any] | None:
    path = _structure_file(library_id)
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > max_age:
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if norm(data.get("target_root") or "") != norm(target_root):
        return None
    return data


def _scan_dirs(source: FileSource, target_root: str) -> list[str]:
    root = norm(target_root)
    if not root:
        return []
    paths: list[str] = []
    try:
        tops = source.list(root)
    except SourceError:
        return paths
    for top in tops:
        if len(paths) >= MAX_TOTAL_DIRS:
            break
        if not top.is_dir or top.name.startswith("."):
            continue
        paths.append(top.name)
        if len(paths) >= MAX_TOP_DIRS or len(paths) >= MAX_TOTAL_DIRS:
            break
        try:
            subs = source.list(join(root, top.name))
        except SourceError:
            continue
        sub_count = 0
        for sub in subs:
            if len(paths) >= MAX_TOTAL_DIRS:
                break
            if not sub.is_dir or sub.name.startswith("."):
                continue
            paths.append(f"{top.name}/{sub.name}")
            sub_count += 1
            if sub_count >= MAX_SUB_DIRS:
                break
    return paths


def build_structure(
    library_id: int,
    source: FileSource,
    target_root: str,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    """读取或生成整理后目录结构快照；失败时返回空结构，不影响整理。"""
    if not refresh:
        cached = _read_cache(library_id, target_root, STRUCTURE_TTL)
        if cached is not None:
            return cached
    paths = _scan_dirs(source, target_root)
    data = {
        "library_id": int(library_id),
        "target_root": norm(target_root),
        "updated_at": now(),
        "paths": paths,
    }
    try:
        path = _structure_file(library_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False))
    except OSError:
        pass
    return data


def _detail_text(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("name") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _validate_path(source: FileSource, target_root: str, path: str, allowed: list[str]) -> bool:
    value = norm(path)
    if value not in {norm(item) for item in allowed}:
        return False
    if value.count("/") > 1:
        return False
    if any(part.startswith(".") for part in value.split("/") if part):
        return False
    if not value:
        return True
    info = source.stat(join(target_root, value))
    return bool(info and info.is_dir)


def suggest_category(
    library: dict[str, Any],
    source: FileSource,
    match: dict[str, Any],
    details: dict[str, Any] | None,
    *,
    structure: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """让模型从现有分类里选一个；只返回建议，是否保存由调用方决定。"""
    mode = str(get_setting("ai_organize_mode", "suggest") or "suggest")
    if mode == "off":
        return None
    target_root = norm(library.get("target_root") or "")
    key = work_key(match)
    if not target_root or not key:
        return None
    if structure is None:
        structure = build_structure(int(library["id"]), source, target_root)
    paths = [str(item) for item in (structure.get("paths") or []) if str(item).strip()]
    if not paths:
        return None
    details = details or {}
    payload = {
        "paths": paths,
        "work": {
            "title": match.get("title") or "",
            "original_title": match.get("original_title") or "",
            "year": match.get("year"),
            "media_type": match.get("media_type") or "",
            "genres": _detail_text(details.get("genres")),
            "original_language": details.get("original_language") or "",
            "origin_country": _detail_text(details.get("origin_country")),
            "overview": str(details.get("overview") or match.get("overview") or "")[:300],
        },
    }
    try:
        data = chat_json(
            SUGGEST_PROMPT,
            payload,
            context="organize",
            file_name=str(match.get("title") or ""),
        )
    except AIError:
        return None
    if not data:
        return None
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))
    path = str(data.get("path") or "").strip()
    try:
        path = normalize_target_path(path, "category")
    except ValueError:
        return None
    if not _validate_path(source, target_root, path, paths):
        return None
    return {
        "path": path,
        "mode": "category",
        "confidence": round(confidence, 4),
        "reason": str(data.get("reason") or "").strip()[:300],
        "source": "ai",
    }


def apply_suggestion(
    library: dict[str, Any],
    match: dict[str, Any],
    suggestion: dict[str, Any],
) -> dict[str, Any] | None:
    """把高置信度的 AI 建议写成作品整理位置，后续新集复用。"""
    key = work_key(match)
    if not key or not suggestion.get("path"):
        return None
    return save_target(
        int(library["id"]),
        key,
        str(suggestion["path"]),
        "category",
        source="ai",
        confidence=suggestion.get("confidence"),
        reason=str(suggestion.get("reason") or ""),
    )
