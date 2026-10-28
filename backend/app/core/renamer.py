"""仅重命名：不移动到目标目录，只改文件名和 / 或所在文件夹名。

与 Organizer 的区别：
- 不使用 library.target_root，也不写刮削素材；
- 文件夹重命名只改当前作品对应的最后 N 层目录，保留更上层的父目录；
- 目录层级不足、文件夹内混有其他作品、目标文件夹已存在时，文件夹重命名会跳过；
- 文件重命名会同步同目录的字幕 / 音轨 / NFO 等附带文件。
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .attachments import attachment_suffix
from .naming import build_target
from .organizer import Organizer, _target_with_suffix, resolve_collision
from .providers.base import FileSource, SourceError, join, norm, parent_of

NON_WORK_DIR_RE = re.compile(
    r"^(?:subs?|subtitles?|字幕|字幕组|extras?|featurettes?|花絮|samples?|scans?|artwork|"
    r"s\d{1,2}e\d{1,3}|e\d{1,3}|cd\d+|disc\d+)$",
    re.IGNORECASE,
)


def _basename(path: str) -> str:
    return norm(path).rsplit("/", 1)[-1]


def _same_path_ci(left: str, right: str) -> bool:
    return norm(left).lower() == norm(right).lower()


def _exists_as_different_path(source: FileSource, path: str, current: str) -> bool:
    """目标存在且不是当前文件本身。仅大小写不同的路径在部分文件系统上视为同一个。"""
    if not source.exists(path):
        return False
    return norm(path) != norm(current) and not _same_path_ci(path, current)


@dataclass
class FolderStep:
    old_path: str
    new_path: str

    def to_dict(self) -> dict[str, str]:
        return {"old_path": self.old_path, "new_path": self.new_path}


@dataclass
class RenamePlan:
    id: int
    file_name: str
    source_path: str
    target_path: str
    desired_file_name: str
    folder_steps: list[FolderStep] = field(default_factory=list)
    file_changed: bool = False
    folder_changed: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def changed(self) -> bool:
        return self.file_changed or self.folder_changed

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_name": self.file_name,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "desired_file_name": self.desired_file_name,
            "folder_steps": [step.to_dict() for step in self.folder_steps],
            "file_changed": self.file_changed,
            "folder_changed": self.folder_changed,
            "changed": self.changed,
            "warnings": self.warnings,
            "error": self.error,
        }


def collect_folder_steps(plans: list[RenamePlan]) -> list[FolderStep]:
    """合并多个条目重复的文件夹改名，并按从深到浅排序。

    先改 Season 目录，再改上层剧集 / 电影目录，避免父目录改名后子目录旧路径失效。
    """
    merged: dict[str, FolderStep] = {}
    for plan in plans:
        for step in plan.folder_steps:
            key = norm(step.old_path)
            if key in merged:
                if norm(merged[key].new_path) != norm(step.new_path):
                    # 同一个目录被算出不同目标，保留第一个，调用方会在计划里看到冲突。
                    continue
                continue
            merged[key] = step
    return sorted(
        merged.values(),
        key=lambda step: (-step.old_path.count("/"), step.old_path),
    )


def apply_folder_step(source: FileSource, step: FolderStep) -> bool:
    old_path, new_path = norm(step.old_path), norm(step.new_path)
    if old_path == new_path:
        return False
    if not source.exists(old_path):
        raise SourceError(f"源文件夹不存在: {old_path}")
    if _exists_as_different_path(source, new_path, old_path):
        raise SourceError(f"目标文件夹已存在: {new_path}")
    source.move(old_path, new_path)
    return True


def build_rename_plan(
    *,
    source: FileSource,
    naming: dict[str, Any],
    item: dict[str, Any],
    parsed: dict[str, Any],
    match: dict[str, Any],
    rename_files: bool,
    rename_folders: bool,
    root_path: str = "",
    episode_title: str = "",
    folder_guard: Callable[[str], bool] | None = None,
    target_name: str = "",
) -> RenamePlan:
    source_path = norm(item.get("source_path") or "")
    file_name = item.get("file_name") or _basename(source_path)
    plan = RenamePlan(
        id=int(item.get("id") or 0),
        file_name=file_name,
        source_path=source_path,
        target_path=source_path,
        desired_file_name=file_name,
    )

    if not source_path:
        plan.error = "缺少源路径"
        return plan
    if not match or not match.get("tmdb_id"):
        plan.error = "未匹配 TMDb，无法计算名称"
        return plan
    status = str(item.get("status") or "")
    if status == "organized":
        plan.error = "已整理条目不支持仅重命名"
        return plan
    if status in {"ignored", "missing"}:
        plan.error = "该条目已忽略或源文件缺失"
        return plan

    extension = posixpath.splitext(source_path)[1] or ".mkv"
    desired_folder, desired_name = build_target(
        parsed,
        match,
        naming,
        extension=extension,
        episode_title=episode_title,
        source_name=_basename(source_path),
    )
    desired_segments = [part for part in desired_folder.split("/") if part.strip()]
    desired_name = target_name or desired_name or _basename(source_path)
    plan.desired_file_name = desired_name

    old_folder = parent_of(source_path)
    old_segments = [part for part in old_folder.split("/") if part.strip()]
    final_folder = old_folder
    folder_steps: list[FolderStep] = []
    folder_changed = False
    skipped_reason = ""

    if rename_folders:
        level_count = len(desired_segments)
        if not level_count:
            skipped_reason = "未配置文件夹命名模板，文件夹重命名未生效"
        elif len(old_segments) < level_count:
            skipped_reason = (
                f"当前目录只有 {len(old_segments)} 层，按当前规则需要 {level_count} 层，"
                "文件夹重命名未生效"
            )
        elif old_segments and NON_WORK_DIR_RE.match(old_segments[-1]):
            skipped_reason = "当前视频位于字幕 / 花絮 / 分段等子目录，文件夹重命名未生效"
        else:
            prefix = old_segments[:-level_count]
            old_tail = old_segments[-level_count:]
            old_top = join(*(prefix + old_tail[:1]))
            root = norm(root_path)
            if root and old_top == root:
                skipped_reason = "目录层级不足或文件直接位于影视库根目录，文件夹重命名未生效"
            elif root and not old_top.startswith(root.rstrip("/") + "/"):
                skipped_reason = "文件不在影视库根目录内，文件夹重命名未生效"
            elif folder_guard is not None and not folder_guard(old_top):
                skipped_reason = "目录内包含其他作品或未识别视频，文件夹重命名未生效"
            else:
                new_folder = join(*(prefix + desired_segments))
                if norm(new_folder) == norm(old_folder):
                    folder_changed = False
                elif _exists_as_different_path(source, new_folder, old_folder):
                    skipped_reason = "目标文件夹已存在，文件夹重命名未生效"
                else:
                    pending_steps: list[FolderStep] = []
                    for index in range(1, level_count + 1):
                        old_level = join(*(prefix + old_tail[:index]))
                        # 原地只改当前这一层的名字；更深的子目录随父目录一起移动。
                        # 执行时从深到浅，先改 Season，再改上层剧集 / 电影目录。
                        new_level = join(
                            *(prefix + old_tail[: index - 1] + [desired_segments[index - 1]])
                        )
                        if norm(old_level) == norm(new_level):
                            continue
                        if _exists_as_different_path(source, new_level, old_level):
                            skipped_reason = (
                                f"目标文件夹已存在，文件夹重命名未生效: {new_level}"
                            )
                            pending_steps = []
                            break
                        pending_steps.append(FolderStep(old_level, new_level))
                    if not skipped_reason:
                        folder_steps = pending_steps
                        folder_changed = bool(folder_steps)
                        if folder_changed:
                            final_folder = new_folder

    if skipped_reason:
        plan.warnings.append(skipped_reason)

    plan.folder_steps = folder_steps
    plan.folder_changed = folder_changed

    file_target = join(final_folder, desired_name)
    file_changed = bool(rename_files and norm(desired_name) != norm(_basename(source_path)))
    plan.file_changed = file_changed

    if file_changed:
        if _exists_as_different_path(source, file_target, source_path):
            file_target = resolve_collision(source, file_target)
        plan.target_path = file_target
    else:
        plan.target_path = join(final_folder, _basename(source_path))

    if not plan.changed:
        plan.target_path = source_path
    return plan


@dataclass
class FileRenameResult:
    source_path: str
    target_path: str
    moved_sidecars: int = 0
    moved_assets: int = 0
    moved_paths: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def rename_file_in_place(
    *,
    source: FileSource,
    naming: dict[str, Any],
    source_path: str,
    parsed: dict[str, Any],
    desired_name: str,
    manual_links: dict[str, str] | None = None,
) -> FileRenameResult:
    """只改视频文件名，附带文件跟随；不处理文件夹级海报 / NFO。"""
    source_path = norm(source_path)
    current_name = _basename(source_path)
    result = FileRenameResult(source_path=source_path, target_path=source_path)
    if not desired_name or norm(desired_name) == norm(current_name):
        return result

    target_path = join(parent_of(source_path), desired_name)
    if _exists_as_different_path(source, target_path, source_path):
        target_path = resolve_collision(source, target_path)
    result.target_path = target_path

    # 在移动前读取附件归属，避免移动后目录内容发生变化。
    organizer = Organizer(
        source,
        naming,
        {"enabled": False},
        manual_links=manual_links,
    )
    attachments = organizer._attachments_for(source_path, parsed)
    sidecars = list(attachments.by_video.get(norm(source_path), []))
    source_stem = posixpath.splitext(current_name)[0]

    source.move(source_path, target_path)
    result.moved_paths[source_path] = target_path

    for attachment in sidecars:
        if not source.exists(attachment.path):
            continue
        suffix = attachment_suffix(attachment.name, source_stem)
        planned = _target_with_suffix(target_path, suffix)
        if attachment.kind == "meta" and source.exists(planned):
            # 刮削或已有的 NFO 不覆盖，也不生成 (1).nfo
            continue
        if norm(attachment.path) == norm(planned):
            result.moved_sidecars += 1
            continue
        sidecar_target = planned
        if _exists_as_different_path(source, planned, attachment.path):
            sidecar_target = resolve_collision(source, planned)
        try:
            source.move(attachment.path, sidecar_target)
        except Exception as exc:  # SourceError / OSError 都交给调用方汇总
            result.warnings.append(f"附带文件重命名失败: {attachment.name} ({exc})")
            continue
        result.moved_sidecars += 1
        result.moved_paths[attachment.path] = sidecar_target
    return result
