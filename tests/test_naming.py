"""命名模板与整理目标的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_naming.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.naming import (  # noqa: E402
    DEFAULT_NAMING,
    build_tags,
    build_target,
    refresh_tech_fields,
    sanitize_component,
)
from app.core.organizer import Organizer, _target_with_suffix, resolve_collision  # noqa: E402
from app.core.parser import parse_media_name  # noqa: E402
from app.core.providers.base import Entry, FileSource, norm  # noqa: E402


class FakeSource(FileSource):
    """只用于测试重名处理的假文件源。"""

    type = "fake"

    def __init__(self, existing: set[str]):
        super().__init__({})
        self.existing = existing

    def list(self, path: str = "") -> list:
        return []

    def exists(self, path: str) -> bool:
        return path in self.existing


class OrganizeSource(FileSource):
    """内存文件源：用于验证失败重试时不重复移动视频、继续移动字幕。"""

    type = "fake"

    def __init__(self, files: set[str]):
        super().__init__({})
        self.files = {norm(path) for path in files}
        self.moves: list[tuple[str, str]] = []

    def list(self, path: str = "") -> list[Entry]:
        parent = norm(path)
        result = []
        for file_path in self.files:
            file_parent, _, name = file_path.rpartition("/")
            if file_parent == parent:
                result.append(Entry(name=name, path=file_path, is_dir=False, size=1024))
        return result

    def exists(self, path: str) -> bool:
        return norm(path) in self.files

    def mkdir(self, path: str) -> None:
        return

    def move(self, src: str, dst: str) -> None:
        src, dst = norm(src), norm(dst)
        self.moves.append((src, dst))
        self.files.discard(src)
        self.files.add(dst)


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    passed = 0
    total = 0

    # ---- 电影默认模板 ----
    parsed_movie = {
        "title": "Oppenheimer",
        "year": 2023,
        "resolution": "2160p",
        "source": "web-dl",
        "audio": "ddp5.1",
        "media_type": "movie",
    }
    match_movie = {
        "title": "奥本海默",
        "original_title": "Oppenheimer",
        "year": 2023,
        "media_type": "movie",
        "tmdb_id": 872585,
    }
    folder, filename = build_target(parsed_movie, match_movie, DEFAULT_NAMING, extension=".mkv")
    total += 1
    passed += check("movie folder", folder, "奥本海默 (2023)")
    total += 1
    passed += check("movie file", filename, "奥本海默 (2023) 2160p.web-dl.ddp5.1.mkv")

    # ---- include_tmdb_id ----
    _, filename_id = build_target(
        parsed_movie,
        match_movie,
        {**DEFAULT_NAMING, "include_tmdb_id": True},
        extension=".mkv",
    )
    total += 1
    passed += check(
        "movie tmdb id",
        filename_id,
        "奥本海默 (2023) 2160p.web-dl.ddp5.1.{tmdbid-872585}.mkv",
    )

    # 没有附加信息时 TMDB ID 仍用空格衔接
    _, filename_id_plain = build_target(
        parsed_movie,
        match_movie,
        {
            **DEFAULT_NAMING,
            "include_tmdb_id": True,
            "movie_file": "{title} ({year})",
        },
        extension=".mkv",
    )
    total += 1
    passed += check(
        "movie tmdb id without tags",
        filename_id_plain,
        "奥本海默 (2023) {tmdbid-872585}.mkv",
    )

    # ---- TMDB ID 标签样式 ----
    _, filename_id_square = build_target(
        parsed_movie,
        match_movie,
        {
            **DEFAULT_NAMING,
            "include_tmdb_id": True,
            "tmdb_id_template": "[tmdbid={id}]",
        },
        extension=".mkv",
    )
    total += 1
    passed += check(
        "movie tmdb id style",
        filename_id_square,
        "奥本海默 (2023) 2160p.web-dl.ddp5.1.[tmdbid=872585].mkv",
    )

    # ---- 视频质量（HDR/DV）标签 ----
    parsed_hdr = {
        "title": "Lord of War",
        "year": 2005,
        "resolution": "2160p",
        "hdr": "dv.hdr",
        "source": "uhd",
        "audio": "dts",
        "media_type": "movie",
    }
    tags_hdr = build_tags(parsed_hdr, DEFAULT_NAMING)
    total += 1
    passed += check("hdr in default tags", tags_hdr, ".2160p.dv.hdr.uhd.dts")

    # ---- 剧集默认模板 ----
    parsed_tv = {
        "title": "庆余年",
        "year": 2019,
        "resolution": "2160p",
        "source": "webdl",
        "media_type": "tv",
        "season": 1,
        "episode": 2,
    }
    match_tv = {"title": "庆余年", "year": 2019, "media_type": "tv", "tmdb_id": 84864}
    folder_tv, filename_tv = build_target(
        parsed_tv, match_tv, DEFAULT_NAMING, extension=".mkv", episode_title="初见京都"
    )
    total += 1
    passed += check("tv folder", folder_tv, "庆余年 (2019)/Season 1")
    total += 1
    passed += check(
        "tv file",
        filename_tv,
        "庆余年 S01E02 - 初见京都.2160p.webdl.mkv",
    )

    # ---- 集标题关闭 ----
    _, filename_no_ep = build_target(
        parsed_tv,
        match_tv,
        {**DEFAULT_NAMING, "episode_title": False},
        extension=".mkv",
        episode_title="初见京都",
    )
    total += 1
    passed += check("tv episode title off", filename_no_ep, "庆余年 S01E02.2160p.webdl.mkv")

    # ---- 剧集 TMDB ID 与附加信息的衔接 ----
    _, filename_tv_id = build_target(
        parsed_tv,
        match_tv,
        {**DEFAULT_NAMING, "include_tmdb_id": True},
        extension=".mkv",
    )
    total += 1
    passed += check(
        "tv tmdb id",
        filename_tv_id,
        "庆余年 S01E02.2160p.webdl.{tmdbid-84864}.mkv",
    )

    # ---- 文件夹点选样式（界面不暴露占位符） ----
    for style, expected in (
        ("{title} ({year})", "庆余年 (2019)/Season 1"),
        ("{title}", "庆余年/Season 1"),
        ("{title} {year}", "庆余年 2019/Season 1"),
    ):
        folder_style, _ = build_target(
            parsed_tv, match_tv, {**DEFAULT_NAMING, "tv_folder": style}, extension=".mkv"
        )
        total += 1
        passed += check(f"tv folder style {style!r}", folder_style, expected)

    for style, expected in (
        ("Season {season}", "庆余年 (2019)/Season 1"),
        ("Season {season:02d}", "庆余年 (2019)/Season 01"),
        ("S{season}", "庆余年 (2019)/S1"),
        ("第{season}季", "庆余年 (2019)/第1季"),
        ("", "庆余年 (2019)"),
    ):
        folder_style, _ = build_target(
            parsed_tv, match_tv, {**DEFAULT_NAMING, "season_folder": style}, extension=".mkv"
        )
        total += 1
        passed += check(f"season folder style {style!r}", folder_style, expected)

    for style, expected in (
        ("{title} ({year})", "奥本海默 (2023)"),
        ("{title}", "奥本海默"),
        ("{title} {year}", "奥本海默 2023"),
    ):
        folder_style, _ = build_target(
            parsed_movie, match_movie, {**DEFAULT_NAMING, "movie_folder": style}, extension=".mkv"
        )
        total += 1
        passed += check(f"movie folder style {style!r}", folder_style, expected)

    # ---- 发布信息直接取自原文件名（保留大小写） ----
    parsed_atmos = {
        "title": "Lord of War",
        "year": 2005,
        "resolution": "2160p",
        "source": "uhd",
        "video": "hevc",
        "audio": "dts",
        "media_type": "movie",
        "raw": "Lord.of.War.2005.2160p.UHD.BluRay.REMUX.HEVC.DV.HDR.DTS-HD.MA.TrueHD.7.1.Atmos-年糕.mkv",
    }
    match_lord = {
        "title": "战争之王",
        "original_title": "Lord of War",
        "year": 2005,
        "media_type": "movie",
        "tmdb_id": 1830,
    }
    _, filename_atmos = build_target(parsed_atmos, match_lord, DEFAULT_NAMING, extension=".mkv")
    total += 1
    passed += check(
        "release from raw name",
        filename_atmos,
        "战争之王 (2005) 2160p.UHD.BluRay.REMUX.HEVC.DV.HDR.DTS-HD.MA.TrueHD.7.1.Atmos-年糕.mkv",
    )

    parsed_tv_raw = {**parsed_tv, "raw": "庆余年.S01E02.2160p.WEB-DL.DDP5.1.H.265-XXTV.mkv"}
    _, filename_tv_raw = build_target(
        parsed_tv_raw, match_tv, DEFAULT_NAMING, extension=".mkv", episode_title="初见京都"
    )
    total += 1
    passed += check(
        "release from raw name (tv)",
        filename_tv_raw,
        "庆余年 S01E02 - 初见京都.2160p.WEB-DL.DDP5.1.H.265-XXTV.mkv",
    )

    # 宽×高尺寸按高度统一为 p 写法
    parsed_dimension = parse_media_name("Movie.2023.1920x1080.WEB-DL.mkv").to_dict()
    match_dimension = {
        "title": "Movie",
        "original_title": "Movie",
        "year": 2023,
        "media_type": "movie",
        "tmdb_id": 1,
    }
    _, filename_dimension = build_target(
        parsed_dimension, match_dimension, DEFAULT_NAMING, extension=".mkv"
    )
    total += 1
    passed += check(
        "resolution dimension normalized",
        filename_dimension,
        "Movie (2023) 1080p.WEB-DL.mkv",
    )

    # 标题本身含年份（1917）：按解析出的年份 2019 截取
    parsed_1917 = {
        "title": "1917",
        "year": 2019,
        "raw": "1917.2019.1080p.BluRay.x264.DTS-HD.MA.5.1-FGT.mkv",
        "media_type": "movie",
    }
    match_1917 = {
        "title": "1917",
        "original_title": "1917",
        "year": 2019,
        "media_type": "movie",
        "tmdb_id": 530915,
    }
    _, filename_1917 = build_target(parsed_1917, match_1917, DEFAULT_NAMING, extension=".mkv")
    total += 1
    passed += check(
        "release after title-year", filename_1917, "1917 (2019) 1080p.BluRay.x264.DTS-HD.MA.5.1-FGT.mkv"
    )

    # 全角括号、点号分隔的中文发布名
    parsed_cn_raw = {
        "title": "战争之王",
        "year": 2005,
        "raw": "【战.争.之.王 2005】4K.UHD.BluRay.REMUX.HDR.国语中字.mkv",
        "media_type": "movie",
    }
    _, filename_cn_raw = build_target(parsed_cn_raw, match_lord, DEFAULT_NAMING, extension=".mkv")
    total += 1
    passed += check(
        "release with fullwidth brackets",
        filename_cn_raw,
        "战争之王 (2005) 4K.UHD.BluRay.REMUX.HDR.国语中字.mkv",
    )

    # 无法从文件名截取时回退到 {tags}
    parsed_noyear = {
        "title": "Some Movie",
        "resolution": "1080p",
        "source": "bluray",
        "media_type": "movie",
        "raw": "Some.Movie.1080p.BluRay.x264.mkv",
    }
    _, filename_noyear = build_target(
        parsed_noyear,
        {"title": "Some Movie", "year": 2001, "media_type": "movie", "tmdb_id": 1},
        DEFAULT_NAMING,
        extension=".mkv",
    )
    total += 1
    passed += check(
        "release fallback to tags", filename_noyear, "Some Movie (2001) 1080p.BluRay.x264.mkv"
    )

    # ---- 自定义标签顺序与分隔符 ----
    tags = build_tags(
        parsed_movie,
        {**DEFAULT_NAMING, "tag_order": ["resolution", "audio"], "tag_separator": "-"},
    )
    total += 1
    passed += check("custom tags", tags, "-2160p-ddp5.1")

    # ---- 自选信息拼接：使用原文件名原文，保留大小写 ----
    parsed_case = {
        **parsed_atmos,
        "tag_texts": {
            "resolution": "2160p",
            "hdr": "DV.HDR",
            "source": "UHD.BluRay.REMUX",
            "video": "HEVC",
            "audio": "DTS-HD.MA.TrueHD.Atmos",
            "group": "年糕",
        },
    }
    _, filename_case = build_target(
        parsed_case,
        match_lord,
        {**DEFAULT_NAMING, "movie_file": "{title} ({year}){tags}"},
        extension=".mkv",
    )
    total += 1
    passed += check(
        "original-case tags",
        filename_case,
        "战争之王 (2005) 2160p.DV.HDR.UHD.BluRay.REMUX.HEVC.DTS-HD.MA.TrueHD.Atmos.年糕.mkv",
    )

    # ---- 电影 / 剧集各自独立的字段与分隔符 ----
    naming_split = {
        **DEFAULT_NAMING,
        "movie_file": "{title} ({year}){tags}",
        "episode_file": "{title} S{season:02d}E{episode:02d}{tags}",
        "tag_fields_movie": ["resolution"],
        "tag_fields_tv": ["resolution", "audio"],
        "tag_separator_tv": "-",
    }
    _, filename_split_movie = build_target(parsed_movie, match_movie, naming_split, extension=".mkv")
    total += 1
    passed += check("movie tag fields", filename_split_movie, "奥本海默 (2023) 2160p.mkv")
    _, filename_split_tv = build_target(
        {**parsed_tv, "audio": "ddp5.1"}, match_tv, naming_split, extension=".mkv"
    )
    total += 1
    passed += check("tv tag fields + separator", filename_split_tv, "庆余年 S01E02-2160p-ddp5.1.mkv")

    # ---- 自定义附加信息条目 ----
    naming_custom_tag = {
        **DEFAULT_NAMING,
        "movie_file": "{title} ({year}){tags}",
        "tag_fields_movie": ["resolution", "custom:国语中字"],
    }
    _, filename_custom_tag = build_target(
        parsed_movie, match_movie, naming_custom_tag, extension=".mkv"
    )
    total += 1
    passed += check("custom tag entry", filename_custom_tag, "奥本海默 (2023) 2160p.国语中字.mkv")

    # ---- 标题语言 ----
    _, filename_en = build_target(
        parsed_movie,
        match_movie,
        {**DEFAULT_NAMING, "title_language": "en"},
        extension=".mkv",
    )
    total += 1
    passed += check("english title", filename_en.startswith("Oppenheimer (2023)"), True)

    # ---- 非法字符与保留名 ----
    total += 1
    passed += check("sanitize invalid", sanitize_component('A/B:C*D?"'), "A B C D")
    total += 1
    passed += check("sanitize reserved", sanitize_component("CON"), "_CON")
    total += 1
    passed += check("sanitize empty", sanitize_component("  ... "), "Unknown")

    # ---- 重名冲突 ----
    source = FakeSource({"library/电影/a.mkv", "library/电影/a (1).mkv"})
    total += 1
    passed += check("collision", resolve_collision(source, "library/电影/a.mkv"), "library/电影/a (2).mkv")
    total += 1
    passed += check(
        "collision overwrite",
        resolve_collision(source, "library/电影/a.mkv", overwrite=True),
        "library/电影/a.mkv",
    )

    # ---- 剧集根目录与季目录分离 ----
    organizer = Organizer(source, DEFAULT_NAMING, {})
    total += 1
    passed += check(
        "show folder",
        organizer.show_folder_for(match_tv, target_root="library"),
        "library/庆余年 (2019)",
    )

    # ---- 陈旧解析缓存：按需从原文件名补齐技术信息，且不动上下文字段 ----
    parsed_stale = {
        "raw": "Lord.of.War.2005.2160p.UHD.BluRay.REMUX.HEVC.DV.HDR.DTS-HD.MA.TrueHD.7.1.Atmos-年糕.mkv",
        "title": "Lord of War",
        "year": 2005,
        "resolution": "2160p",
        "source": "uhd",
        "video": "hevc",
        "audio": "dts-hd.ma",
        "group": "年糕",
    }
    refreshed = refresh_tech_fields(parsed_stale)
    total += 1
    passed += check("refresh fills hdr", refreshed.get("hdr"), "dv.hdr")
    total += 1
    passed += check(
        "refresh keeps context",
        (refreshed.get("title"), refreshed.get("year")),
        ("Lord of War", 2005),
    )
    _, filename_stale = build_target(
        parsed_stale,
        match_lord,
        {**DEFAULT_NAMING, "movie_file": "{title} ({year}){tags}"},
        extension=".mkv",
    )
    total += 1
    passed += check(
        "stale cache original case",
        filename_stale,
        "战争之王 (2005) 2160p.DV.HDR.UHD.BluRay.REMUX.HEVC.DTS-HD.MA.TrueHD.Atmos.年糕.mkv",
    )
    parsed_modern = parse_media_name(
        "Lord.of.War.2005.2160p.UHD.BluRay.REMUX.HEVC.DV.HDR.DTS-HD.MA.TrueHD.7.1.Atmos-年糕.mkv"
    ).to_dict()
    total += 1
    passed += check("modern cache untouched", refresh_tech_fields(parsed_modern) is parsed_modern, True)

    # ---- 失败重试：视频已在目标位置时不再生成 (1)，继续移动字幕 ----
    parsed_retry = {
        "title": "Lord of War",
        "year": 2005,
        "resolution": "2160p",
        "source": "uhd",
        "media_type": "movie",
    }
    naming_retry = {**DEFAULT_NAMING, "auto_category": False}
    probe = Organizer(FakeSource(set()), naming_retry, {"enabled": False})
    retry_source = "待整理/Lord.of.War.2005.mkv"
    retry_sidecar = "待整理/Lord.of.War.2005.sup"
    retry_folder, retry_target = probe.target_for(
        parsed_retry,
        match_lord,
        source_path=retry_source,
        target_root="library",
    )
    retry_sidecar_target = _target_with_suffix(retry_target, ".sup")
    retry = OrganizeSource({retry_target, retry_sidecar})
    retry_result = Organizer(retry, naming_retry, {"enabled": False}).organize(
        source_path=retry_source,
        parsed=parsed_retry,
        match=match_lord,
        target_root="library",
    )
    total += 1
    passed += check(
        "retry keeps existing target",
        (retry_result.target_path, retry.moves),
        (retry_target, [(retry_sidecar, retry_sidecar_target)]),
    )
    total += 1
    passed += check(
        "retry moves sidecar only",
        (retry_target in retry.files, retry_sidecar_target in retry.files),
        (True, True),
    )
    total += 1
    passed += check(
        "retry warning",
        any("已在目标位置" in warning for warning in (retry_result.warnings or [])),
        True,
    )

    # 正常首次整理仍然移动视频和字幕
    fresh = OrganizeSource({retry_source, retry_sidecar})
    fresh_result = Organizer(fresh, naming_retry, {"enabled": False}).organize(
        source_path=retry_source,
        parsed=parsed_retry,
        match=match_lord,
        target_root="library",
    )
    total += 1
    passed += check(
        "fresh organize moves video then sidecar",
        (fresh_result.target_path, fresh.moves),
        (
            retry_target,
            [
                (retry_source, retry_target),
                (retry_sidecar, retry_sidecar_target),
            ],
        ),
    )

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
