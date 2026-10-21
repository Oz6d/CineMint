"""视频附带文件识别与匹配。

视频目录里除了正片，还常有字幕、外挂音轨、海报、NFO 等文件。
这里负责三件事：
1. 按扩展名把文件归为字幕 / 音轨 / 图片 / 元信息；
2. 在同一目录的多部视频之间，尽量把附带文件匹配到正确的视频；
3. 给出整理时可直接使用的文件名后缀（保留语言、字幕组等原文大小写）。

匹配原则偏保守：同名、季集号、唯一视频目录是强匹配；多视频目录里
无法区分归属的字幕会进入「未关联」，不会被强行塞给某一集。
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .parser import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    chinese_to_int,
    file_ext,
    normalize_title,
    normalize_unicode,
    parse_media_name,
)
from .providers.base import Entry, SourceError, norm

SUBTITLE_EXTS = {
    ".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup", ".sbv", ".smi",
}
AUDIO_EXTS = {
    ".mka", ".ac3", ".eac3", ".dts", ".dtshd", ".flac", ".aac", ".m4a",
    ".mp3", ".opus", ".wav", ".thd", ".truehd",
}
META_EXTS = {".nfo"}
ATTACHMENT_IMAGE_EXTS = {*IMAGE_EXTS, ".bmp", ".gif", ".tif", ".tiff"}

KIND_ORDER = ("subtitle", "audio", "image", "meta")
KIND_LABELS = {
    "subtitle": "字幕",
    "audio": "音轨",
    "image": "图片",
    "meta": "元信息",
}

# 常见字幕子目录：只扫描一层，并且只接收字幕 / 音轨。
# 子目录里的 poster.jpg / movie.nfo 不应被当成某一集的附件。
ATTACHMENT_SUBDIRS = {
    "subs", "sub", "subtitles", "subtitle", "字幕", "字幕文件",
}

# 这些名字通常属于整个作品 / 文件夹，而不是某一集：
# 整理时保留原文件名，放到视频所在的目标文件夹。
GENERIC_IMAGE_STEMS = {
    "poster", "folder", "fanart", "backdrop", "banner", "landscape",
    "clearlogo", "logo", "clearart", "disc", "discart", "cdart", "cover",
    "thumb", "keyart", "character", "movie", "tvshow", "show", "season", "episode",
}
GENERIC_IMAGE_WORDS = {
    "poster", "cover", "fanart", "backdrop", "banner", "logo",
    "海报", "封面", "背景", "剧照", "壁纸", "wallpaper",
}
GENERIC_META_STEMS = {"movie", "tvshow", "show", "season", "episode"}
# 剧集的作品级资源应放到剧集根目录，而不是某一季文件夹。
SHOW_LEVEL_ASSET_STEMS = {
    "tvshow", "show", "fanart", "backdrop", "banner", "clearlogo", "logo",
    "clearart", "landscape", "keyart", "character",
}

# 截图、二维码、频道图这类图片不是影片附件，避免整理时被当成海报带走。
NOISE_IMAGE_HINTS = {
    "截图", "截屏", "screenshot", "sample", "预览", "preview",
    "二维码", "qrcode", "扫码", "公众号", "频道", "频道图",
}

# 语言 / 字幕属性词。提取后缀时保留原大小写与顺序。
LANG_HINTS = {
    "zh", "chs", "cht", "chi", "zho", "sc", "tc", "cn",
    "en", "eng", "ja", "jpn", "jp", "ko", "kor", "kr",
    "fr", "fre", "fra", "ger", "de", "es", "spa", "rus", "ru",
    "ita", "por", "pt", "pl", "nld", "dut", "swe", "nor", "dan",
    "简", "繁", "简中", "繁中", "中英", "中字", "双语", "中日",
    "中文", "英文", "中文字幕", "英文字幕", "中英双语", "中英双字", "简繁", "简繁中", "简繁英",
    "英语", "国语", "粤语", "日语", "韩语", "法语", "德语", "俄语",
    "国粤双语", "国语粤语", "简体", "繁体", "简英", "繁英", "简日", "繁日", "简韩",
    "hans", "hant", "cmn", "yue",
    "forced", "sdh", "cc", "commentary", "comment", "default",
    "dialogue", "signs", "songs",
}

NOISE_TOKENS = {
    "the", "and", "a", "an", "of", "to", "in", "on", "for", "with",
}

# 视频文件里常见的非集数数字
RESOLUTION_NUMBERS = {264, 265, 480, 576, 720, 1080, 1440, 2160, 4320}


@dataclass
class Attachment:
    name: str
    path: str
    kind: str
    size: int = 0
    scope: str = "file"  # file / folder
    reason: str = ""
    manual: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "kind_label": KIND_LABELS.get(self.kind, "文件"),
            "size": self.size,
            "scope": self.scope,
            "reason": self.reason,
            "manual": self.manual,
        }


@dataclass
class VideoRef:
    path: str
    name: str
    parsed: dict[str, Any]


@dataclass
class DirectoryAttachments:
    by_video: dict[str, list[Attachment]]
    folder_assets: list[Attachment]
    unmatched: list[Attachment]
    videos: list[VideoRef] = field(default_factory=list)

    def payload_for(self, source_path: str) -> dict[str, Any]:
        key = norm(source_path)
        items = sorted(
            self.by_video.get(key, []),
            key=lambda item: (KIND_ORDER.index(item.kind) if item.kind in KIND_ORDER else 99, item.name.lower()),
        )
        folder_items = sorted(
            self.folder_assets,
            key=lambda item: (KIND_ORDER.index(item.kind) if item.kind in KIND_ORDER else 99, item.name.lower()),
        )
        unmatched = sorted(
            self.unmatched,
            key=lambda item: (KIND_ORDER.index(item.kind) if item.kind in KIND_ORDER else 99, item.name.lower()),
        )
        counts = {kind: 0 for kind in KIND_ORDER}
        for item in [*items, *folder_items]:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return {
            "items": [item.to_dict() for item in items],
            "folder_items": [item.to_dict() for item in folder_items],
            "unmatched": [item.to_dict() for item in unmatched],
            "counts": counts,
            "total": len(items) + len(folder_items),
            "current_path": norm(source_path),
            "videos": [
                {
                    "path": video.path,
                    "name": video.name,
                    "season": video.parsed.get("season"),
                    "episode": video.parsed.get("episode"),
                }
                for video in self.videos
            ],
        }


def _stem(name: str) -> str:
    stem, _ = posixpath.splitext(name)
    return stem


def classify_attachment(name: str) -> str:
    ext = file_ext(name)
    if ext in SUBTITLE_EXTS:
        return "subtitle"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in ATTACHMENT_IMAGE_EXTS:
        return "image"
    if ext in META_EXTS:
        return "meta"
    return ""


def is_attachment_subdir(name: str) -> bool:
    """是否是已知的字幕 / 音轨子目录名。"""
    return normalize_unicode(name).strip().lower() in ATTACHMENT_SUBDIRS


def is_attachment_directory(video_dir: str, attachment_dir: str) -> bool:
    """附件目录必须是视频同目录，或视频目录下一层的已知字幕子目录。"""
    video = norm(video_dir)
    target = norm(attachment_dir)
    if target == video:
        return True
    return (
        posixpath.dirname(target) == video
        and is_attachment_subdir(posixpath.basename(target))
    )


def collect_attachment_entries(source: Any, directory: str) -> list[Entry]:
    """列出视频目录 + 常见字幕子目录里的附件。

    子目录只接收字幕 / 音轨，且只扫描一层；图片 / NFO 仍按文件夹级资源处理。
    直接复用第一次 list() 的结果找子目录，避免 WebDAV 重复 PROPFIND。
    """
    entries = list(source.list(directory))
    for entry in list(entries):
        if not entry.is_dir or not is_attachment_subdir(entry.name):
            continue
        try:
            children = source.list(entry.path)
        except (SourceError, OSError, ValueError):
            continue
        for child in children:
            if child.is_dir:
                continue
            if classify_attachment(child.name) in {"subtitle", "audio"}:
                entries.append(child)
    return entries


def is_noise_image(name: str) -> bool:
    stem = normalize_unicode(_stem(name)).lower()
    return any(hint in stem for hint in NOISE_IMAGE_HINTS)


def is_generic_asset(name: str) -> bool:
    """海报 / 背景图 / tvshow.nfo 这类文件夹级资源。"""
    ext = file_ext(name)
    stem = normalize_unicode(_stem(name)).strip().lower()
    if ext in ATTACHMENT_IMAGE_EXTS:
        if stem in GENERIC_IMAGE_STEMS:
            return True
        return any(word in stem for word in GENERIC_IMAGE_WORDS)
    if ext in META_EXTS:
        return stem in GENERIC_META_STEMS
    return False


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.split(r"[^0-9a-z\u4e00-\u9fff]+", normalize_unicode(text).lower())
        if token
    ]


def _plausible_episode(value: int) -> bool:
    if value < 0 or value > 999:
        return False
    if 1900 <= value <= 2100:
        return False
    return value not in RESOLUTION_NUMBERS


def _episode_hint(name: str) -> tuple[int | None, int | None]:
    """从文件名提取 (季, 集)。只用于辅助匹配，不替代 parser。"""
    text = normalize_unicode(_stem(name))
    season: int | None = None
    episode: int | None = None

    match = re.search(r"(?i)s(\d{1,2})[ ._-]*e(?:p)?[ ._-]?(\d{1,4})", text)
    if match:
        return int(match.group(1)), int(match.group(2))

    match = re.search(r"(?i)(?<![0-9])(\d{1,2})x(\d{1,4})(?![0-9])", text)
    if match:
        season_value = int(match.group(1))
        episode_value = int(match.group(2))
        if _plausible_episode(episode_value):
            return season_value, episode_value

    match = re.search(
        r"(?i)(?:^|[ ._\-\[\(（【])e(?:p|pisode)?[ ._-]?(\d{1,4})(?=$|[ ._\-\[\]\)）】])",
        text,
    )
    if match:
        value = int(match.group(1))
        if _plausible_episode(value):
            episode = value

    match = re.search(r"第\s*([零〇一二两三四五六七八九十百\d]+)\s*[集话話]", text)
    if match:
        value = chinese_to_int(match.group(1))
        if value is not None:
            episode = value

    match = re.search(r"(?<![0-9])(\d{1,4})\s*[集话話]", text)
    if match and episode is None:
        value = int(match.group(1))
        if _plausible_episode(value):
            episode = value

    # 1920x1080 / 640x480 这类画面尺寸不是季集。
    text = re.sub(
        r"(?i)(?<![0-9])(?:\d{3,4}x\d{3,4}|\d{1,2}x\d{3,4}|\d{3,4}x\d{1,2})(?![0-9])",
        " ",
        text,
    )

    # DDP5.1 / TrueHD.7.1 / AAC2.0 里的声道数字不是集数，先去掉。
    # 这里只匹配独立的声道写法，避免误伤 S01E01.1080p 这类文件名。
    text = re.sub(
        r"(?i)(?<![a-z0-9])(?:dd\+?|ddp|dts(?:-hd)?|truehd|atmos|aac|ac3|eac3|flac|opus|mp3)?"
        r"[ ._-]?[1257]\.[01](?![0-9])",
        " ",
        text,
    )

    match = re.search(
        r"(?i)(?:^|[ ._\-\[\(（【])s(?:eason)?[ ._-]?(\d{1,2})(?=$|[ ._\-\[\]\)）】])",
        text,
    )
    if match:
        season = int(match.group(1))

    if episode is None:
        match = re.search(r"[\[\(（【]\s*(\d{1,4})\s*[\]\)）】]", text)
        if match:
            value = int(match.group(1))
            if _plausible_episode(value):
                episode = value

    if episode is None:
        tokens = [token for token in re.split(r"[ ._\-\[\]()（）【】]+", text) if token]
        numeric: list[int] = []
        for index, token in enumerate(tokens):
            if not token.isdigit():
                continue
            if index > 0 and tokens[index - 1].lower() in {"season", "s"}:
                continue
            value = int(token)
            if _plausible_episode(value):
                numeric.append(value)
        # 只有一个候选数字时才当集数；5.1 / 01.02 这种多数字命名过于含糊，
        # 宁可显示未关联，也不要把字幕塞给错误的集数。
        if len(numeric) == 1:
            episode = numeric[0]

    return season, episode


def _video_episode(video: VideoRef) -> tuple[int | None, int | None]:
    season = video.parsed.get("season")
    episode = video.parsed.get("episode")
    hint_season, hint_episode = _episode_hint(video.name)
    if season is None:
        season = hint_season
    if episode is None:
        episode = hint_episode
    return (
        int(season) if season is not None else None,
        int(episode) if episode is not None else None,
    )


def _starts_with_boundary(text: str, prefix: str) -> bool:
    if not text.startswith(prefix):
        return False
    if len(text) == len(prefix):
        return True
    return text[len(prefix)] in "._- []()（）【】"


def _common_prefix_length(left: str, right: str) -> int:
    length = 0
    for first, second in zip(left, right):
        if first != second:
            break
        length += 1
    return length


def _title_matches(title: str, sidecar_name: str) -> bool:
    normalized = normalize_title(title or "")
    if not normalized:
        return False
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", normalized))
    if has_cjk:
        return len(normalized) >= 2 and normalized in normalize_title(sidecar_name)
    if len(normalized) < 3 or normalized in NOISE_TOKENS:
        return False
    sidecar_tokens = set(_tokens(sidecar_name))
    if normalized in sidecar_tokens:
        return True
    title_tokens = _tokens(title)
    if not title_tokens:
        return False
    return all(token in sidecar_tokens for token in title_tokens)


def _score_attachment(video: VideoRef, sidecar_name: str) -> tuple[int, str]:
    video_stem = normalize_unicode(_stem(video.name)).lower()
    sidecar_stem = normalize_unicode(_stem(sidecar_name)).lower()
    score = 0
    reasons: list[str] = []

    if video_stem and sidecar_stem:
        if video_stem == sidecar_stem:
            return 120, "同名"
        if len(sidecar_stem) >= 6 and _starts_with_boundary(sidecar_stem, video_stem + "."):
            score = max(score, 100)
            reasons.append("主文件名匹配")
        elif len(video_stem) >= 6 and _starts_with_boundary(video_stem, sidecar_stem + "."):
            score = max(score, 95)
            reasons.append("主文件名匹配")
        elif min(len(video_stem), len(sidecar_stem)) >= 8 and (
            video_stem in sidecar_stem or sidecar_stem in video_stem
        ):
            score = max(score, 85)
            reasons.append("主文件名匹配")
        else:
            common = _common_prefix_length(video_stem, sidecar_stem)
            if common >= 8 and common >= min(len(video_stem), len(sidecar_stem)) * 0.6:
                # 只作为弱提示；同一部剧相邻集的文件名天然有很长的公共前缀，
                # 不能让它压过真正的季集匹配。
                score = max(score, 50)
                reasons.append("文件名高度相似")

    video_season, video_episode = _video_episode(video)
    sidecar_season, sidecar_episode = _episode_hint(sidecar_name)
    if video_episode is not None and sidecar_episode is not None and video_episode == sidecar_episode:
        if video_season is None or sidecar_season is None or video_season == sidecar_season:
            score = max(score, 80)
            reasons.append("季集匹配")

    for key in ("title", "title_alt"):
        if _title_matches(str(video.parsed.get(key) or ""), sidecar_name):
            score = max(score, 45)
            reasons.append("标题匹配")
            break

    return score, "、".join(dict.fromkeys(reasons))


def video_refs_from_entries(
    entries: Iterable[Entry],
    *,
    current_path: str = "",
    current_parsed: dict[str, Any] | None = None,
) -> list[VideoRef]:
    refs: list[VideoRef] = []
    seen: set[str] = set()
    current_key = norm(current_path) if current_path else ""
    for entry in entries:
        if entry.is_dir or file_ext(entry.name) not in VIDEO_EXTS:
            continue
        key = norm(entry.path)
        parsed_name = parse_media_name(entry.name)
        # 样本 / 预告 / 花絮不是正片，不能让它们抢走正片的字幕。
        if parsed_name.is_extra and key != current_key:
            continue
        parsed = parsed_name.to_dict()
        if current_key and key == current_key and current_parsed:
            parsed = {**parsed, **current_parsed}
        refs.append(VideoRef(path=key, name=entry.name, parsed=parsed))
        seen.add(key)
    if current_key and current_key not in seen:
        name = posixpath.basename(current_key)
        parsed = parse_media_name(name).to_dict()
        if current_parsed:
            parsed = {**parsed, **current_parsed}
        refs.append(VideoRef(path=current_key, name=name, parsed=parsed))
    refs.sort(
        key=lambda ref: (
            int(ref.parsed.get("season") or 0),
            int(ref.parsed.get("episode") or 0),
            ref.name.lower(),
        )
    )
    return refs


def match_directory(
    entries: Iterable[Entry],
    videos: list[VideoRef],
    manual_links: dict[str, str] | None = None,
) -> DirectoryAttachments:
    folder_assets: list[Attachment] = []
    candidates: list[tuple[Entry, str]] = []
    for entry in entries:
        if entry.is_dir:
            continue
        kind = classify_attachment(entry.name)
        if not kind:
            continue
        if kind == "image" and is_noise_image(entry.name):
            continue
        folder_level = is_generic_asset(entry.name)
        attachment = Attachment(
            name=entry.name,
            path=entry.path,
            kind=kind,
            size=entry.size,
            scope="folder" if folder_level else "file",
            reason="文件夹级资源" if folder_level else "",
        )
        if attachment.scope == "folder":
            folder_assets.append(attachment)
        else:
            candidates.append((entry, kind))

    by_video: dict[str, list[Attachment]] = {norm(video.path): [] for video in videos}
    unmatched: list[Attachment] = []
    if not videos:
        return DirectoryAttachments(by_video={}, folder_assets=folder_assets, unmatched=[
            Attachment(name=entry.name, path=entry.path, kind=kind, size=entry.size, reason="同目录没有视频")
            for entry, kind in candidates
        ], videos=videos)

    # 手动关联优先于自动匹配。只接受同一目录里实际存在的视频目标；
    # 如果手动关联的视频已被移走/改名，就退回自动匹配。
    manual = {norm(key): norm(value) for key, value in (manual_links or {}).items()}
    if manual:
        remaining: list[tuple[Entry, str]] = []
        for entry, kind in candidates:
            target = manual.get(norm(entry.path))
            if target and target in by_video:
                by_video[target].append(
                    Attachment(
                        name=entry.name,
                        path=entry.path,
                        kind=kind,
                        size=entry.size,
                        reason="手动关联",
                        manual=True,
                    )
                )
            else:
                remaining.append((entry, kind))
        candidates = remaining

    if len(videos) == 1:
        video = videos[0]
        for entry, kind in candidates:
            if kind == "image" and _score_attachment(video, entry.name)[0] <= 0:
                unmatched.append(
                    Attachment(
                        name=entry.name,
                        path=entry.path,
                        kind=kind,
                        size=entry.size,
                        reason="图片与视频名称无关",
                    )
                )
                continue
            by_video[norm(video.path)].append(
                Attachment(
                    name=entry.name,
                    path=entry.path,
                    kind=kind,
                    size=entry.size,
                    reason="唯一视频目录",
                )
            )
        return DirectoryAttachments(by_video=by_video, folder_assets=folder_assets, unmatched=unmatched, videos=videos)

    for entry, kind in candidates:
        scores = sorted(
            (
                (score, index, reason)
                for index, video in enumerate(videos)
                for score, reason in [_score_attachment(video, entry.name)]
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        best = scores[0]
        second = scores[1] if len(scores) > 1 else (0, -1, "")
        # 强匹配（同名 / 季集）直接归位；标题等弱匹配必须和次优候选拉开差距，
        # 否则一部剧的多集共享同一个标题时会把字幕随意塞给第一集。
        confident = best[0] >= 75 and best[0] - second[0] >= 20
        unique_weak = best[0] >= 40 and best[0] - second[0] >= 25
        if confident or unique_weak:
            by_video[norm(videos[best[1]].path)].append(
                Attachment(
                    name=entry.name,
                    path=entry.path,
                    kind=kind,
                    size=entry.size,
                    reason=best[2],
                )
            )
        else:
            unmatched.append(
                Attachment(
                    name=entry.name,
                    path=entry.path,
                    kind=kind,
                    size=entry.size,
                    reason="同目录有多个视频，文件名无法区分",
                )
            )

    return DirectoryAttachments(by_video=by_video, folder_assets=folder_assets, unmatched=unmatched, videos=videos)


def load_directory_attachments(
    source: Any,
    source_path: str,
    parsed: dict[str, Any] | None = None,
    manual_links: dict[str, str] | None = None,
) -> DirectoryAttachments:
    """读取视频所在目录（含常见字幕子目录）并完成匹配。"""
    directory = posixpath.dirname(norm(source_path))
    entries = collect_attachment_entries(source, directory)
    videos = video_refs_from_entries(
        entries,
        current_path=source_path,
        current_parsed=parsed or {},
    )
    return match_directory(entries, videos, manual_links=manual_links)


def attachment_suffix(sidecar_name: str, source_stem: str) -> str:
    """整理后保留的附加后缀，例如 .chs.ass / .国语.mka / -thumb.jpg。"""
    ext = file_ext(sidecar_name)
    name_stem = _stem(sidecar_name)
    if source_stem and name_stem.lower().startswith(source_stem.lower()):
        rest = sidecar_name[len(source_stem):]
        return rest or ext

    pieces = [
        piece
        for piece in re.split(r"[._\-\s\[\]【】()（）&+]+", name_stem)
        if piece
    ]
    hints: list[str] = []
    for piece in pieces:
        lower = piece.lower()
        if (lower in LANG_HINTS or piece in LANG_HINTS) and piece not in hints:
            hints.append(piece)
    if hints:
        return "." + ".".join(hints) + ext
    if any(piece.lower() in {"thumb", "thumbnail"} for piece in pieces):
        return "-thumb" + ext
    return ext
