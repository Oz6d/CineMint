"""订阅暂存目录的通用路径计算（夸克 / 115 共用）。"""

from __future__ import annotations

import re

from .providers.base import norm


def sanitize_component(value: str, fallback: str = "未命名") -> str:
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (value or "").strip())
    text = text.strip(" .")
    return text[:80] or fallback


def default_save_subdir(name: str) -> str:
    return f"_订阅/{sanitize_component(name)}"


def safe_save_subdir(value: str, name: str) -> str:
    """把用户填写的暂存子目录规范成影视库根目录下的相对路径。"""
    raw = norm(value or "") or default_save_subdir(name)
    parts: list[str] = []
    for part in raw.split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        parts.append(sanitize_component(part))
    return "/".join(parts) or default_save_subdir(name)


def cloud_root_parts(library_root: str, cloud_root: str = "") -> list[str]:
    """云盘侧影视库根目录分段：默认去掉第一段 OpenList 挂载名。"""
    override = norm(cloud_root)
    if override:
        return [part for part in override.split("/") if part]
    root_parts = [part for part in norm(library_root).split("/") if part]
    return root_parts[1:]


def cloud_root_prefix(library_root: str, cloud_root: str = "") -> str:
    return "/".join(cloud_root_parts(library_root, cloud_root))


def map_cloud_folder(
    library_root: str, cloud_root: str, folder_path: str
) -> str | None:
    """云盘绝对目录 → 影视库内相对目录；不在库内返回 None。"""
    base = cloud_root_parts(library_root, cloud_root)
    folder = [part for part in norm(folder_path).split("/") if part]
    if folder[: len(base)] != base:
        return None
    return "/".join(folder[len(base):])


def staging_paths(
    library_root: str, save_subdir: str, *, cloud_root: str = ""
) -> tuple[str, list[str]]:
    """返回 OpenList 暂存路径与云盘侧目录分段。

    默认把影视库根路径的第一段视为 OpenList 挂载名（例如
    ``夸克网盘/待整理`` → ``待整理``）；可用 cloud_root 显式覆盖。
    """
    root = norm(library_root)
    relative = norm(save_subdir)
    openlist_path = norm("/".join(part for part in (root, relative) if part))
    base_parts = cloud_root_parts(root, cloud_root)
    cloud_parts = base_parts + [part for part in relative.split("/") if part]
    return openlist_path, cloud_parts
