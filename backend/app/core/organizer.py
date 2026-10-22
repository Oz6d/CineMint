from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Any, Iterable

from .attachments import (
    SHOW_LEVEL_ASSET_STEMS,
    Attachment,
    DirectoryAttachments,
    attachment_suffix,
    collect_attachment_entries,
    match_directory,
    video_refs_from_entries,
)
from .classify import SUB_MIN_SCORE, TOP_MIN_SCORE, attributes_for, best_folder, pick_other
from .naming import DEFAULT_SCRAPE, build_target, sanitize_component
from .parser import file_ext
from .providers import FileSource, SourceError, join, norm, parent_of
from .scraper import (
    ImageFetcher,
    episode_nfo,
    movie_nfo,
    season_nfo,
    tvshow_nfo,
)


def _splitext(name: str) -> tuple[str, str]:
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    return name[:dot], name[dot:]


def _target_with_suffix(target: str, suffix: str) -> str:
    target_stem, target_ext = _splitext(posixpath.basename(target))
    if suffix.lower().endswith((".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup")):
        suffix_ext = file_ext(suffix)
        prefix = suffix[: -len(suffix_ext)] if suffix_ext else suffix
        prefix = prefix.strip(".")
        new_name = target_stem + (("." + prefix) if prefix else "") + suffix_ext
    else:
        new_name = target_stem + suffix
    return join(parent_of(target), new_name)


def resolve_collision(
    source: FileSource, target: str, overwrite: bool = False, *, target_exists: bool | None = None
) -> str:
    if target_exists is None:
        target_exists = source.exists(target)
    if overwrite or not target_exists:
        return target
    stem, ext = _splitext(target)
    for index in range(1, 100):
        candidate = f"{stem} ({index}){ext}"
        if not source.exists(candidate):
            return candidate
    raise SourceError(f"目标重名过多: {target}")


@dataclass
class OrganizeResult:
    source_path: str
    target_path: str
    sidecars: int = 0
    folder_assets: int = 0
    attachment_counts: dict[str, int] | None = None
    warnings: list[str] | None = None
    moved_paths: dict[str, str] | None = None
    removed_dirs: list[str] | None = None


class Organizer:
    def __init__(
        self,
        source: FileSource,
        naming: dict[str, Any],
        scrape: dict[str, Any],
        images: ImageFetcher | None = None,
        manual_links: dict[str, str] | None = None,
        source_root: str = "",
    ):
        self.source = source
        self.naming = naming or {}
        self.scrape = {**DEFAULT_SCRAPE, **(scrape or {})}
        self.images = images or ImageFetcher()
        self.source_root = norm(source_root)
        self.manual_links = {
            norm(attachment): norm(video)
            for attachment, video in (manual_links or {}).items()
        }
        self._dir_cache: dict[str, list[Any]] = {}
        self._top_dir_cache: dict[str, list[str]] = {}
        self._sub_dir_cache: dict[str, list[str]] = {}
        self._written: set[str] = set()

    def _entries(self, directory: str) -> list[Any]:
        key = norm(directory)
        if key not in self._dir_cache:
            try:
                self._dir_cache[key] = collect_attachment_entries(self.source, directory)
            except SourceError:
                self._dir_cache[key] = []
        return self._dir_cache[key]

    def _forget_cached_paths(self, paths: list[str]) -> None:
        """移动成功后同步目录缓存，整理同目录多集时不重复处理已移动文件。"""
        moved = {norm(path) for path in paths if path}
        if not moved:
            return
        for entries in self._dir_cache.values():
            entries[:] = [entry for entry in entries if norm(entry.path) not in moved]

    def _child_dir_names(self, path: str, cache: dict[str, list[str]]) -> list[str]:
        key = norm(path)
        if key not in cache:
            try:
                cache[key] = [entry.name for entry in self.source.list_dirs(path)]
            except SourceError:
                cache[key] = []
        return cache[key]

    def detect_structure(self, target_root: str, max_top: int = 15) -> list[dict[str, Any]]:
        """识别整理后目录的两级结构，用于设置页展示。"""
        if not target_root:
            return []
        tree: list[dict[str, Any]] = []
        for name in self._child_dir_names(target_root, self._top_dir_cache)[:max_top]:
            children = self._child_dir_names(join(target_root, name), self._sub_dir_cache)
            tree.append({"name": name, "children": children[:80]})
        return tree

    def category_prefix(
        self,
        match: dict[str, Any],
        target_root: str,
        details: dict[str, Any] | None = None,
    ) -> str:
        """按目标目录已有结构选择分类目录（如 电影/欧美电影）；识别不到时返回空串。"""
        if not target_root or not self.naming.get("auto_category", True):
            return ""
        attr = attributes_for(match, details)
        tops = self._child_dir_names(target_root, self._top_dir_cache)
        parent, _ = best_folder(tops, attr, min_score=TOP_MIN_SCORE)
        if not parent:
            return ""
        subs = self._child_dir_names(join(target_root, parent), self._sub_dir_cache)
        sub, _ = best_folder(subs, attr, min_score=SUB_MIN_SCORE, require_region_or_genre=True)
        if not sub:
            sub = pick_other(subs)
        return f"{parent}/{sub}" if sub else parent

    def attachments_for(self, source_path: str, parsed: dict[str, Any]) -> dict[str, Any]:
        """当前视频的附带文件 + 文件夹级资源 + 未关联文件。"""
        directory = parent_of(source_path)
        entries = self._entries(directory)
        videos = video_refs_from_entries(
            entries,
            current_path=source_path,
            current_parsed=parsed,
        )
        matched = match_directory(entries, videos, manual_links=self.manual_links)
        return matched.payload_for(source_path)

    def _attachments_for(self, source_path: str, parsed: dict[str, Any]) -> DirectoryAttachments:
        directory = parent_of(source_path)
        entries = self._entries(directory)
        videos = video_refs_from_entries(
            entries,
            current_path=source_path,
            current_parsed=parsed,
        )
        return match_directory(entries, videos, manual_links=self.manual_links)

    def find_sidecars(self, source_path: str, parsed: dict[str, Any]) -> list[str]:
        """兼容旧调用：返回会随视频移动的单个文件（不含文件夹级海报/NFO）。"""
        matched = self._attachments_for(source_path, parsed)
        return [attachment.path for attachment in matched.by_video.get(norm(source_path), [])]

    def find_folder_assets(self, source_path: str, parsed: dict[str, Any]) -> list[Attachment]:
        matched = self._attachments_for(source_path, parsed)
        return list(matched.folder_assets)

    def target_for(
        self,
        parsed: dict[str, Any],
        match: dict[str, Any],
        *,
        source_path: str,
        episode_title: str = "",
        target_root: str = "",
        details: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        extension = file_ext(source_path) or ".mkv"
        source_name = source_path.replace("\\", "/").rsplit("/", 1)[-1]
        folder, filename = build_target(
            parsed,
            match,
            self.naming,
            extension=extension,
            episode_title=episode_title,
            source_name=source_name,
        )
        parts = [part for part in (self.category_prefix(match, target_root, details), folder) if part]
        folder_path = join(target_root, *parts) if target_root else join(*parts)
        return folder_path, join(folder_path, filename)

    def _write_asset(self, folder: str, name: str, data: bytes | None) -> None:
        if not data:
            return
        path = join(folder, name)
        if path in self._written:
            return
        if self.source.exists(path) and not self.scrape.get("overwrite", False):
            self._written.add(path)
            return
        self.source.write(path, data)
        self._written.add(path)

    def scrape_show(
        self,
        folder: str,
        match: dict[str, Any],
        details: dict[str, Any],
        season_details: dict[int, dict[str, Any]],
    ) -> None:
        if not self.scrape.get("enabled", True):
            return
        media_type = match.get("media_type") or "movie"
        poster = details.get("poster_path") or match.get("poster_path") or ""
        backdrop = details.get("backdrop_path") or match.get("backdrop_path") or ""
        logo = details.get("logo_path") or match.get("logo_path") or ""
        title = details.get("title") or match.get("title") or ""
        year = details.get("year") or match.get("year") or ""
        self.source.mkdir(folder)

        # 电影 NFO 依赖最终目标文件名，统一在 organize() 里写。
        if media_type == "tv" and self.scrape.get("write_nfo", True):
            self._write_asset(
                folder,
                "tvshow.nfo",
                tvshow_nfo(details, poster=bool(poster), fanart=bool(backdrop)).encode("utf-8"),
            )

        poster_bytes = None
        if self.scrape.get("download_poster", True) and poster:
            poster_bytes = self.images.fetch(poster, "w780", title, str(year))
            self._write_asset(folder, "poster.jpg", poster_bytes)
            if self.scrape.get("folder_image", True):
                self._write_asset(folder, "folder.jpg", poster_bytes)
        if self.scrape.get("download_fanart", True) and backdrop:
            self._write_asset(
                folder,
                "fanart.jpg",
                self.images.fetch(backdrop, "w1280", title, str(year)),
            )
        if self.scrape.get("download_logo", True) and logo:
            self._write_asset(folder, "logo.png", self.images.fetch(logo, "original", title, str(year)))

        if media_type == "tv":
            for season_number, season in season_details.items():
                season_folder = join(
                    folder,
                    self._season_folder_name(season_number, match),
                )
                self.source.mkdir(season_folder)
                if self.scrape.get("write_nfo", True):
                    self._write_asset(
                        season_folder,
                        "season.nfo",
                        season_nfo(season_number, season).encode("utf-8"),
                    )
                if self.scrape.get("download_season_poster", True):
                    season_poster = season.get("poster_path") or poster
                    self._write_asset(
                        season_folder,
                        "poster.jpg",
                        self.images.fetch(season_poster, "w780", title, f"Season {season_number}"),
                    )

    def show_folder_for(
        self, match: dict[str, Any], target_root: str = "", details: dict[str, Any] | None = None
    ) -> str:
        """剧集/电影的作品根目录（不含 Season 子目录），用于写作品级海报和剧集 NFO。"""
        from .naming import render, template_values

        values = template_values({}, match, self.naming)
        media_type = match.get("media_type") or "movie"
        key = "tv_folder" if media_type == "tv" else "movie_folder"
        folder = render(self.naming.get(key) or ("{title} ({year})"), values)
        folder = "/".join(sanitize_component(part) for part in str(folder).split("/") if part.strip())
        parts = [part for part in (self.category_prefix(match, target_root, details), folder) if part]
        return join(target_root, *parts) if target_root else join(*parts)

    def _season_folder_name(self, season_number: int, match: dict[str, Any]) -> str:
        from .naming import render, template_values

        values = template_values(
            {},
            match,
            self.naming,
            season=season_number,
            episode=1,
        )
        return render(self.naming.get("season_folder") or "Season {season}", values)

    def scrape_episode(
        self,
        target_path: str,
        match: dict[str, Any],
        season_number: int,
        episode_number: int,
        episode: dict[str, Any] | None,
    ) -> None:
        if not self.scrape.get("enabled", True):
            return
        folder = parent_of(target_path)
        stem, _ = _splitext(posixpath.basename(target_path))
        if self.scrape.get("write_nfo", True):
            self._write_asset(
                folder,
                stem + ".nfo",
                episode_nfo(match, season_number, episode_number, episode).encode("utf-8"),
            )
        if self.scrape.get("download_episode_thumb", True) and episode and episode.get("still_path"):
            title = episode.get("name") or match.get("title") or ""
            data = self.images.fetch(
                episode["still_path"], "w780", title, f"S{season_number:02d}E{episode_number:02d}"
            )
            self._write_asset(folder, stem + "-thumb.jpg", data)

    def organize(
        self,
        *,
        source_path: str,
        parsed: dict[str, Any],
        match: dict[str, Any],
        target_root: str,
        episode_title: str = "",
        season_number: int | None = None,
        episode_number: int | None = None,
        episode: dict[str, Any] | None = None,
        overwrite: bool = False,
        details: dict[str, Any] | None = None,
    ) -> OrganizeResult:
        warnings: list[str] = []
        folder, planned_target = self.target_for(
            parsed,
            match,
            source_path=source_path,
            episode_title=episode_title,
            target_root=target_root,
            details=details,
        )
        source_exists = self.source.exists(source_path)
        target_exists = self.source.exists(planned_target)
        same_path = norm(source_path) == norm(planned_target)
        # 上一次整理可能已把视频移动到最终位置，但后续步骤或回报失败。
        # 重试时直接续做关联文件与刮削，避免生成 «(1)» 重名文件。
        # 目标路径与源路径相同时（原地整理）也不应该把视频改成 (1)。
        already_moved = target_exists and (same_path or not source_exists)
        if already_moved:
            target_path = planned_target
            if not same_path:
                warnings.append("源文件已不在待整理目录，按其已在目标位置的实际情况继续整理")
        else:
            target_path = resolve_collision(
                self.source, planned_target, overwrite, target_exists=target_exists
            )
        self.source.mkdir(folder)

        moved_paths: dict[str, str] = {}
        if norm(source_path) != norm(target_path):
            moved_paths[norm(source_path)] = norm(target_path)

        matched = self._attachments_for(source_path, parsed)
        file_attachments = matched.by_video.get(norm(source_path), [])
        folder_assets = matched.folder_assets
        available = {
            norm(entry.path)
            for entry in self._entries(parent_of(source_path))
            if not entry.is_dir
        }

        moved_sidecars = 0
        moved_folder_assets = 0
        attachment_counts: dict[str, int] = {}
        if not already_moved and norm(source_path) != norm(target_path):
            self.source.move(source_path, target_path)
            self._forget_cached_paths([source_path])

        source_stem, _ = _splitext(posixpath.basename(source_path))
        for attachment in file_attachments:
            if norm(attachment.path) not in available:
                # 上一次整理已经移动过这个文件，继续处理剩下的即可
                continue
            suffix = attachment_suffix(attachment.name, source_stem)
            planned = _target_with_suffix(target_path, suffix)
            if norm(attachment.path) == norm(planned):
                # 已经在目标位置且名字正确，不需要再移动
                moved_sidecars += 1
                attachment_counts[attachment.kind] = attachment_counts.get(attachment.kind, 0) + 1
                continue
            # 刮削已经写好的 NFO / 图片不覆盖，也不生成 (1)
            if attachment.kind == "meta" and self.source.exists(planned) and not overwrite:
                continue
            sidecar_target = resolve_collision(self.source, planned, overwrite)
            try:
                self.source.move(attachment.path, sidecar_target)
                moved_sidecars += 1
                attachment_counts[attachment.kind] = attachment_counts.get(attachment.kind, 0) + 1
                moved_paths[norm(attachment.path)] = norm(sidecar_target)
                self._forget_cached_paths([attachment.path])
            except SourceError as exc:
                warnings.append(f"附带文件移动失败: {attachment.name} ({exc})")

        # 海报 / fanart / tvshow.nfo 等是整个文件夹共用的资源，保留原文件名。
        # 目标已有同名文件（通常是刮削刚写好的）时直接保留，不覆盖。
        show_folder: str | None = None
        for asset in folder_assets:
            if norm(asset.path) not in available:
                continue
            asset_folder = folder
            asset_stem = _splitext(asset.name)[0].lower()
            if match.get("media_type") == "tv" and asset_stem in SHOW_LEVEL_ASSET_STEMS:
                if show_folder is None:
                    show_folder = self.show_folder_for(match, target_root, details=details)
                    self.source.mkdir(show_folder)
                asset_folder = show_folder
            asset_target = join(asset_folder, asset.name)
            if self.source.exists(asset_target) and not overwrite:
                continue
            try:
                self.source.move(asset.path, asset_target)
                moved_folder_assets += 1
                attachment_counts[asset.kind] = attachment_counts.get(asset.kind, 0) + 1
                moved_paths[norm(asset.path)] = norm(asset_target)
                self._forget_cached_paths([asset.path])
            except SourceError as exc:
                warnings.append(f"文件夹附带文件移动失败: {asset.name} ({exc})")

        # 电影 NFO 与最终视频同名；放在移动之后，重名 (1) 时也能对应上。
        if (
            (match.get("media_type") or "movie") != "tv"
            and self.scrape.get("enabled", True)
            and self.scrape.get("write_nfo", True)
        ):
            try:
                poster = (details or {}).get("poster_path") or match.get("poster_path") or ""
                backdrop = (details or {}).get("backdrop_path") or match.get("backdrop_path") or ""
                stem, _ = _splitext(posixpath.basename(target_path))
                self._write_asset(
                    parent_of(target_path),
                    stem + ".nfo",
                    movie_nfo(
                        details or match,
                        poster=bool(poster),
                        fanart=bool(backdrop),
                    ).encode("utf-8"),
                )
            except SourceError as exc:
                warnings.append(f"电影刮削失败: {exc}")

        if match.get("media_type") == "tv" and season_number and episode_number:
            try:
                self.scrape_episode(target_path, match, season_number, episode_number, episode)
            except SourceError as exc:
                warnings.append(f"剧集刮削失败: {exc}")

        removed_dirs: list[str] = []
        if self.naming.get("cleanup_empty_dirs") and self.source_root:
            cleanup_targets = set(moved_paths)
            cleanup_targets.add(norm(source_path))
            removed_dirs, cleanup_warnings = remove_empty_source_dirs(
                self.source,
                cleanup_targets,
                self.source_root,
                target_root,
            )
            warnings.extend(cleanup_warnings)

        return OrganizeResult(
            source_path=source_path,
            target_path=target_path,
            sidecars=moved_sidecars,
            folder_assets=moved_folder_assets,
            attachment_counts=attachment_counts or None,
            warnings=warnings,
            moved_paths=moved_paths or None,
            removed_dirs=removed_dirs or None,
        )


def _is_within(path: str, root: str) -> bool:
    path, root = norm(path), norm(root)
    return bool(root) and (path == root or path.startswith(root + "/"))


def remove_empty_source_dirs(
    source: FileSource,
    moved_paths: Iterable[str],
    source_root: str,
    target_root: str = "",
) -> tuple[list[str], list[str]]:
    """删除整理后变空的源目录，最深层的目录优先。

    只处理被移动文件所在目录及其上级，不扫描整个待整理目录；源根目录和
    目标目录本身不会被删除，无法访问或非空目录只记录警告。
    """
    root = norm(source_root)
    if not root:
        return [], []
    target = norm(target_root)
    guarded_target = target if target and _is_within(target, root) else ""

    candidates: set[str] = set()
    for moved in moved_paths:
        current = parent_of(norm(moved))
        while current:
            if current == root or not _is_within(current, root):
                break
            if guarded_target and _is_within(current, guarded_target):
                break
            candidates.add(current)
            current = parent_of(current)

    removed: list[str] = []
    warnings: list[str] = []
    for directory in sorted(candidates, key=lambda item: (item.count("/"), item), reverse=True):
        try:
            info = source.stat(directory)
            if not info or not info.is_dir or info.is_symlink:
                continue
            if source.list(directory):
                continue
            source.remove_empty_dir(directory)
            removed.append(directory)
        except (SourceError, OSError) as exc:
            warnings.append(f"清理空目录失败 {directory}: {exc}")
    return removed, warnings


def clear_dir_cache(organizer: Organizer) -> None:
    organizer._dir_cache.clear()
