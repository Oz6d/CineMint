"""手动附带文件关联的持久化。

自动匹配只根据文件名工作。遇到 `简体.ass`、`繁体.ass` 这种
在多集目录里无法区分归属的字幕，用户可以手动把它挂到某一集。
数据库只存「视频路径 -> 附带文件路径」，整理时按这个映射强制执行。
"""

from __future__ import annotations

from ..db import execute, now, query_all
from .providers.base import norm


def load_attachment_links(library_id: int) -> dict[str, str]:
    """返回 {附带文件路径: 视频路径}，两边都已规范化。"""
    rows = query_all(
        "SELECT attachment_path, video_path FROM attachment_links WHERE library_id=?",
        (library_id,),
    )
    return {
        norm(row["attachment_path"]): norm(row["video_path"])
        for row in rows
        if row["attachment_path"] and row["video_path"]
    }


def upsert_attachment_link(
    library_id: int,
    *,
    video_path: str,
    attachment_path: str,
) -> None:
    """把附带文件挂到视频；同一文件重复关联时直接改归属。"""
    execute(
        """
        INSERT INTO attachment_links(library_id, video_path, attachment_path, created_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(library_id, attachment_path) DO UPDATE SET
            video_path=excluded.video_path,
            created_at=excluded.created_at
        """,
        (library_id, norm(video_path), norm(attachment_path), now()),
    )


def delete_attachment_link(library_id: int, attachment_path: str) -> None:
    execute(
        "DELETE FROM attachment_links WHERE library_id=? AND attachment_path=?",
        (library_id, norm(attachment_path)),
    )


def delete_library_attachment_links(library_id: int) -> None:
    execute("DELETE FROM attachment_links WHERE library_id=?", (library_id,))
