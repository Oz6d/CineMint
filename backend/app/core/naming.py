from __future__ import annotations

import posixpath
import re
import string
from typing import Any

from .parser import normalize_resolution_dimensions

INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def normalize_target_name(value: str | None, fallback_name: str = "") -> str:
    """校验用户手动填写的整理后文件名；空字符串表示恢复自动命名。"""
    name = (value or "").strip()
    if not name:
        return ""
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("整理后名称只能是文件名，不能包含路径分隔符")
    if INVALID_CHARS.search(name):
        raise ValueError('整理后名称不能包含 < > : " | ? * 等字符')
    if len(name) > 255:
        raise ValueError("整理后名称不能超过 255 个字符")
    fallback_ext = posixpath.splitext(fallback_name or "")[1]
    if fallback_ext and not posixpath.splitext(name)[1]:
        name += fallback_ext
    return name


COLLISION_POLICIES = ("suffix", "skip", "overwrite")
DEFAULT_COLLISION = "suffix"


def normalize_collision(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in COLLISION_POLICIES else DEFAULT_COLLISION


DEFAULT_NAMING: dict[str, Any] = {
    "movie_folder": "{title} ({year})",
    "movie_file": "{title} ({year}){release}",
    "tv_folder": "{title} ({year})",
    "season_folder": "Season {season}",
    "episode_file": "{title} S{season:02d}E{episode:02d}{episode_title}{release}",
    "title_language": "zh",
    "tag_order": ["resolution", "hdr", "source", "edition", "release_notes", "video", "audio", "group"],
    "tag_separator": ".",
    "include_tmdb_id": False,
    "tmdb_id_template": "{tmdbid-{id}}",
    "episode_title": True,
    "cleanup_empty_dirs": False,
    "auto_category": True,
    "collision": DEFAULT_COLLISION,
}

DEFAULT_SCRAPE: dict[str, Any] = {
    "enabled": True,
    "write_nfo": True,
    "download_poster": True,
    "download_fanart": True,
    "download_logo": True,
    "download_season_poster": True,
    "download_episode_thumb": True,
    "folder_image": True,
    "overwrite": False,
}


class _Formatter(string.Formatter):
    def get_value(self, key: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        if isinstance(key, str):
            return kwargs.get(key, "")
        return super().get_value(key, args, kwargs)


def render(template: str, values: dict[str, Any]) -> str:
    if not template:
        return ""
    try:
        return _Formatter().vformat(template, (), values)
    except (ValueError, KeyError, IndexError):
        return template


def sanitize_component(text: str, max_length: int = 150) -> str:
    text = (text or "").strip()
    text = INVALID_CHARS.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip(" .·-")
    if not text:
        text = "Unknown"
    if text.upper() in RESERVED:
        text = "_" + text
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .·-")
    return text


def pick_title(match: dict[str, Any], naming: dict[str, Any]) -> str:
    language = naming.get("title_language", "zh")
    zh = match.get("title") or ""
    original = match.get("original_title") or ""
    if language == "en":
        return original or zh
    if language == "original":
        return original or zh
    return zh or original


TAG_FIELD_KEYS = (
    "resolution", "hdr", "source", "edition", "release_notes", "video", "audio", "group",
)
CUSTOM_TAG_PREFIX = "custom:"


def _tag_value(parsed: dict[str, Any], key: str) -> str:
    """单个附加信息的值：优先使用原文件名里的原文（保留大小写），其次解析出的字段。"""
    if key.startswith(CUSTOM_TAG_PREFIX):
        return key[len(CUSTOM_TAG_PREFIX):].strip()
    original = (parsed.get("tag_texts") or {}).get(key)
    if original:
        return str(original).strip()
    return str(parsed.get(key) or "").strip()


def tag_separator_for(naming: dict[str, Any], media_type: str = "") -> str:
    if media_type == "tv":
        separator = naming.get("tag_separator_tv") or naming.get("tag_separator", ".")
    else:
        separator = naming.get("tag_separator_movie") or naming.get("tag_separator", ".")
    return str(separator or ".")


def build_tags(
    parsed: dict[str, Any],
    naming: dict[str, Any],
    media_type: str = "",
    *,
    lead: str | None = None,
) -> str:
    """拼接附加信息。lead 为附加信息与前面内容的衔接符，默认使用拼接符。"""
    if media_type == "tv":
        order = naming.get("tag_fields_tv") or naming.get("tag_order") or []
    else:
        order = naming.get("tag_fields_movie") or naming.get("tag_order") or []
    separator = tag_separator_for(naming, media_type)
    parts = [_tag_value(parsed, str(key)) for key in order]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    lead = separator if lead is None else lead
    return lead + separator.join(parts)


def _stem_of(name: str) -> str:
    stem = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    dot = stem.rfind(".")
    if dot > 0 and len(stem) - dot <= 5 and stem[dot + 1:].isalnum():
        stem = stem[:dot]
    return stem


def release_tail(source_name: str, parsed: dict[str, Any]) -> str:
    """从原始文件名截取「发布信息」部分（年份 / 季集标记之后的剩余内容），保留原始大小写。

    例：Lord.of.War.2005.2160p.UHD.BluRay.REMUX.HEVC.DV.HDR.DTS-HD.MA.TrueHD.7.1.Atmos-年糕.mkv
    → 2160p.UHD.BluRay.REMUX.HEVC.DV.HDR.DTS-HD.MA.TrueHD.7.1.Atmos-年糕
    """
    stem = _stem_of(source_name)
    if not stem:
        return ""

    cut: int | None = None
    season = parsed.get("season")
    episode = parsed.get("episode")
    if season is not None and episode is not None:
        try:
            pattern = re.compile(
                rf"(?i)s(?:eason)?[ ._-]?0*{int(season)}[ ._-]*e(?:p)?[ ._-]?0*{int(episode)}"
                rf"(?:[ ._-]*e(?:p)?[ ._-]?0*\d{{1,4}})*(?![0-9])"
            )
        except (TypeError, ValueError):
            pattern = None
        match = None
        if pattern is not None:
            for match in pattern.finditer(stem):
                pass
        if match:
            cut = match.end()
        if cut is None:
            # 兜底：任意 SxxExx / 第N集 形式的季集标记，取最后一个
            for generic in (
                r"(?i)s\d{1,2}[ ._-]*e\d{1,4}(?:[ ._-]*e(?:p)?[ ._-]?\d{1,4})*(?![0-9])",
                r"第[ ]?\d{1,4}[ ]?(?:集|话|話|期|回)",
            ):
                match = None
                for match in re.finditer(generic, stem):
                    pass
                if match:
                    cut = match.end()
                    break

    if cut is None:
        year = parsed.get("year")
        if year:
            match = None
            for match in re.finditer(rf"(?<![0-9]){int(year)}(?![0-9])", stem):
                pass
            if match:
                cut = match.end()

    if cut is None:
        return ""
    tail = stem[cut:].lstrip(" .-_]】)）").rstrip(" .")
    # 1920x1080 这类尺寸按高度统一成 1080p，避免整理后仍保留原始尺寸
    return normalize_resolution_dimensions(tail)


def release_value(
    source_name: str,
    parsed: dict[str, Any],
    naming: dict[str, Any],
    media_type: str = "",
    *,
    lead: str | None = None,
) -> str:
    """模板变量 {release}：优先用原文件名的发布信息，提取不到时回退到 {tags}。"""
    tail = release_tail(source_name, parsed)
    if tail:
        separator = tag_separator_for(naming, media_type)
        lead = separator if lead is None else lead
        return lead + tail
    return build_tags(parsed, naming, media_type, lead=lead)


_TECH_FIELDS = (
    "resolution", "hdr", "video", "audio", "source", "edition", "release_notes", "group",
)


def refresh_tech_fields(parsed: dict[str, Any]) -> dict[str, Any]:
    """老条目的解析缓存可能缺少原样文本或新字段，按需从原始文件名补齐技术类信息。

    只补技术类字段（画质/音轨等），标题、季集等上下文推断结果一律保留。
    """
    raw = str(parsed.get("raw") or "")
    texts = parsed.get("tag_texts")
    if not raw or (
        isinstance(texts, dict)
        and all(parsed.get(field) is not None for field in _TECH_FIELDS)
    ):
        return parsed
    try:
        from .parser import parse_media_name

        fresh = parse_media_name(raw).to_dict()
    except Exception:
        return parsed
    merged = dict(parsed)
    for field in _TECH_FIELDS:
        value = fresh.get(field)
        if value not in (None, "") and not merged.get(field):
            merged[field] = value
    if fresh.get("tag_texts"):
        merged["tag_texts"] = fresh["tag_texts"]
    return merged


def template_values(
    parsed: dict[str, Any],
    match: dict[str, Any],
    naming: dict[str, Any],
    *,
    season: int | None = None,
    episode: int | None = None,
    episode_title: str = "",
    source_name: str = "",
) -> dict[str, Any]:
    parsed = refresh_tech_fields(parsed)
    title = pick_title(match, naming)
    media_type = match.get("media_type") or parsed.get("media_type") or "movie"
    episode_value = episode if episode is not None else (parsed.get("episode") or 1)
    season_value = season if season is not None else (parsed.get("season") or 1)
    episode_part = ""
    if naming.get("episode_title", True) and episode_title:
        episode_part = " - " + episode_title
    # 电影：年份（或标题）与附加信息之间默认空一格；剧集：季集标记之后仍用拼接符衔接
    info_lead = " " if media_type != "tv" else None
    return {
        "title": sanitize_component(title),
        "original_title": sanitize_component(match.get("original_title") or title),
        "year": match.get("year") or parsed.get("year") or "",
        "tmdb_id": match.get("tmdb_id") or "",
        "season": season_value,
        "episode": episode_value,
        "episode_title": (" - " + sanitize_component(episode_title)) if episode_part else "",
        "tags": build_tags(parsed, naming, media_type, lead=info_lead),
        "release": release_value(
            source_name or str(parsed.get("raw") or ""),
            parsed,
            naming,
            media_type,
            lead=info_lead,
        ),
        "resolution": parsed.get("resolution") or "",
        "hdr": parsed.get("hdr") or "",
        "source": parsed.get("source") or "",
        "video": parsed.get("video") or "",
        "audio": parsed.get("audio") or "",
        "edition": parsed.get("edition") or "",
        "release_notes": parsed.get("release_notes") or "",
        "group": parsed.get("group") or "",
    }


_TRAILING_INFO_VARS = (
    "release", "tags", "resolution", "hdr", "source", "edition",
    "release_notes", "video", "audio", "group",
)


def _ends_with_added_info(template: str, file_name: str, values: dict[str, Any]) -> bool:
    """文件名末尾是否就是附加信息（用于 TMDB ID 的衔接符）。"""
    match = re.search(
        r"\{(" + "|".join(_TRAILING_INFO_VARS) + r")\}\s*$", template or ""
    )
    if not match:
        return False
    value = str(values.get(match.group(1)) or "").strip()
    return bool(value) and file_name.rstrip().endswith(value)


def build_target(
    parsed: dict[str, Any],
    match: dict[str, Any],
    naming: dict[str, Any],
    *,
    extension: str,
    episode_title: str = "",
    source_name: str = "",
) -> tuple[str, str]:
    """Returns (folder_relative_path, file_name)."""
    media_type = match.get("media_type") or parsed.get("media_type") or "movie"
    values = template_values(
        parsed, match, naming, episode_title=episode_title, source_name=source_name
    )
    if media_type == "tv":
        folder = render(naming.get("tv_folder") or DEFAULT_NAMING["tv_folder"], values)
        season_value = values["season"]
        season_template = naming.get("season_folder")
        if season_template is None:
            season_template = DEFAULT_NAMING["season_folder"]
        season_folder = render(
            season_template,
            {**values, "season": season_value},
        )
        file_template = str(
            naming.get("episode_file") or DEFAULT_NAMING["episode_file"]
        )
        file_name = render(file_template, values)
        parts = [folder]
        if str(season_folder).strip():
            parts.append(season_folder)
    else:
        folder = render(naming.get("movie_folder") or DEFAULT_NAMING["movie_folder"], values)
        file_template = str(naming.get("movie_file") or DEFAULT_NAMING["movie_file"])
        file_name = render(file_template, values)
        parts = [folder]
    if naming.get("include_tmdb_id") and values["tmdb_id"]:
        tmdb_template = str(naming.get("tmdb_id_template") or DEFAULT_NAMING["tmdb_id_template"])
        rendered = tmdb_template.replace("{id}", str(values["tmdb_id"])).strip()
        if rendered:
            # 紧跟附加信息时用 . 衔接（2160p.DV.{tmdbid-…}），没有附加信息时才用空格
            joiner = "." if _ends_with_added_info(file_template, file_name, values) else " "
            file_name = f"{file_name}{joiner}{rendered}"
    file_name = sanitize_component(file_name, max_length=180)
    segments: list[str] = []
    for part in parts:
        for segment in str(part or "").split("/"):
            if segment.strip():
                segments.append(sanitize_component(segment))
    folder_path = "/".join(segments)
    ext = extension if extension.startswith(".") else f".{extension}"
    return folder_path, file_name + ext.lower()
