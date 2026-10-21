"""附带文件识别 / 匹配 / 随视频整理的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_attachments.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.attachments import (  # noqa: E402
    _episode_hint,
    attachment_suffix,
    is_generic_asset,
    match_directory,
    video_refs_from_entries,
)
from app.core.naming import DEFAULT_NAMING  # noqa: E402
from app.core.organizer import Organizer  # noqa: E402
from app.core.providers.base import Entry, FileSource, norm  # noqa: E402


class FakeSource(FileSource):
    type = "fake"

    def __init__(self, files: set[str]):
        super().__init__({})
        self.files = {norm(path) for path in files}
        self.moves: list[tuple[str, str]] = []

    def list(self, path: str = "") -> list[Entry]:
        parent = norm(path)
        entries = []
        for file_path in sorted(self.files):
            file_parent, _, name = file_path.rpartition("/")
            if file_parent == parent:
                entries.append(Entry(name=name, path=file_path, is_dir=False, size=1024))
        return entries

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


def match_files(files: list[str], current: str, parsed: dict, manual_links: dict[str, str] | None = None):
    entries = [
        Entry(name=path.rsplit("/", 1)[-1], path=path, is_dir=False, size=1024)
        for path in files
    ]
    refs = video_refs_from_entries(entries, current_path=current, current_parsed=parsed)
    return match_directory(entries, refs, manual_links=manual_links)


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    # ---- 单视频目录：名字完全不同的字幕 / 外挂音轨也能挂上 ----
    base = "待整理/Movie"
    files = [f"{base}/Movie.2023.1080p.mkv", f"{base}/简体.ass", f"{base}/国语.mka"]
    matched = match_files(files, files[0], {"title": "Movie", "year": 2023, "media_type": "movie"})
    items = matched.by_video[norm(files[0])]
    expect("single video subtitle", [(item.name, item.kind) for item in items], [
        ("简体.ass", "subtitle"),
        ("国语.mka", "audio"),
    ])
    expect("single video no unmatched", matched.unmatched, [])

    # ---- 海报 / NFO 是文件夹级；截图和频道图不能被当成海报 ----
    files = [
        f"{base}/Movie.2023.1080p.mkv",
        f"{base}/poster.jpg",
        f"{base}/movie.nfo",
        f"{base}/字幕截图.png",
        f"{base}/年糕的腾讯频道.jpg",
    ]
    matched = match_files(files, files[0], {"title": "Movie", "year": 2023, "media_type": "movie"})
    expect("folder assets", sorted(item.name for item in matched.folder_assets), ["movie.nfo", "poster.jpg"])
    expect("screenshot ignored", [item.name for item in matched.by_video[norm(files[0])]], [])
    expect("generic poster", is_generic_asset("poster.jpg"), True)
    expect("screenshot not generic", is_generic_asset("字幕截图.png"), False)

    # ---- 样本 / 花絮视频不参与附带文件竞争 ----
    files = [
        f"{base}/Movie.2023.1080p.mkv",
        f"{base}/Movie.2023.sample.mkv",
        f"{base}/简体.ass",
    ]
    matched = match_files(files, files[0], {"title": "Movie", "year": 2023, "media_type": "movie"})
    expect("sample ignored", [item.name for item in matched.by_video[norm(files[0])]], ["简体.ass"])
    expect("sample no unmatched", matched.unmatched, [])

    # ---- 多集目录：1.ass / 2.ass 按集数匹配 ----
    base = "待整理/Show"
    files = [
        f"{base}/Show.S01E01.1080p.mkv",
        f"{base}/Show.S01E02.1080p.mkv",
        f"{base}/1.ass",
        f"{base}/2.ass",
    ]
    matched = match_files(files, files[0], {"title": "Show", "season": 1, "episode": 1, "media_type": "tv"})
    expect(
        "bare episode subtitles",
        [
            (path.rsplit("/", 1)[-1], [item.name for item in items])
            for path, items in matched.by_video.items()
        ],
        [
            ("Show.S01E01.1080p.mkv", ["1.ass"]),
            ("Show.S01E02.1080p.mkv", ["2.ass"]),
        ],
    )

    # ---- 多集目录：带集数的字幕匹配；只有标题、无法区分的字幕不硬塞 ----
    files = [
        f"{base}/Show.S01E01.1080p.mkv",
        f"{base}/Show.S01E02.1080p.mkv",
        f"{base}/Show.S01E01.chs.ass",
        f"{base}/Show.chs.ass",
    ]
    matched = match_files(files, files[0], {"title": "Show", "season": 1, "episode": 1, "media_type": "tv"})
    expect("episode subtitle matched", [item.name for item in matched.by_video[norm(files[0])]], ["Show.S01E01.chs.ass"])
    expect("ambiguous subtitle unmatched", [item.name for item in matched.unmatched], ["Show.chs.ass"])

    # ---- 手动关联：未关联字幕可以直接挂到当前集 ----
    manual_path = f"{base}/Show.chs.ass"
    matched = match_files(
        files,
        files[0],
        {"title": "Show", "season": 1, "episode": 1, "media_type": "tv"},
        manual_links={manual_path: files[0]},
    )
    expect(
        "manual link subtitle",
        {
            (item.name, item.manual)
            for item in matched.by_video[norm(files[0])]
        },
        {("Show.S01E01.chs.ass", False), ("Show.chs.ass", True)},
    )
    expect(
        "manual link reason",
        next(item.reason for item in matched.by_video[norm(files[0])] if item.name == "Show.chs.ass"),
        "手动关联",
    )
    expect("manual link no unmatched", matched.unmatched, [])
    expect(
        "manual link payload flag",
        next(item["manual"] for item in matched.payload_for(files[0])["items"] if item["path"] == manual_path),
        True,
    )

    # ---- 手动关联可以覆盖自动匹配，把 1.ass 改挂到第二集 ----
    manual_files = [f"{base}/Show.S01E01.1080p.mkv", f"{base}/Show.S01E02.1080p.mkv", f"{base}/1.ass"]
    matched = match_files(
        manual_files,
        manual_files[0],
        {"title": "Show", "season": 1, "episode": 1, "media_type": "tv"},
        manual_links={f"{base}/1.ass": manual_files[1]},
    )
    expect(
        "manual override auto match",
        [
            (path.rsplit("/", 1)[-1], [item.name for item in items])
            for path, items in matched.by_video.items()
        ],
        [("Show.S01E01.1080p.mkv", []), ("Show.S01E02.1080p.mkv", ["1.ass"])],
    )

    # ---- 整理时遵守手动关联 ----
    source = FakeSource({manual_files[0], manual_files[1], f"{base}/1.ass"})
    organizer = Organizer(
        source,
        {**DEFAULT_NAMING, "auto_category": False},
        {"enabled": False},
        manual_links={f"{base}/1.ass": manual_files[1]},
    )
    result = organizer.organize(
        source_path=manual_files[1],
        parsed={"title": "Show", "year": 2024, "season": 1, "episode": 2, "media_type": "tv"},
        match={"title": "Show", "year": 2024, "media_type": "tv", "tmdb_id": 9},
        target_root="library",
        season_number=1,
        episode_number=2,
    )
    target_subtitle = result.target_path.rsplit(".", 1)[0] + ".ass"
    expect(
        "manual link moves with organizer",
        (f"{base}/1.ass" not in source.files) and (target_subtitle in source.files),
        True,
    )

    # ---- 整理后保留语言后缀 ----
    expect("language suffix", attachment_suffix("Show.S01E01.chs&eng.ass", "Show.S01E01.1080p"), ".chs.eng.ass")
    expect("audio suffix", attachment_suffix("国语.mka", "Movie.2023.1080p"), ".国语.mka")
    expect("thumb suffix", attachment_suffix("Show.thumb.jpg", "Show.2023.1080p"), "-thumb.jpg")
    expect("bare number suffix", attachment_suffix("1.ass", "Show.S01E01.1080p"), ".ass")

    # ---- 分辨率 / 画面尺寸不能被当成季集 ----
    expect("1920x1080 no episode", _episode_hint("Movie.1920x1080.mkv"), (None, None))
    expect("640x480 no episode", _episode_hint("Movie.640x480.ass"), (None, None))
    expect("1x02 still episode", _episode_hint("Show.1x02.mkv"), (1, 2))

    # ---- 音频声道 5.1 / 7.1 不能被当成集数 ----
    expect("ddp5.1 no episode", _episode_hint("Oppenheimer.2023.2160p.DDP5.1.Atmos.mkv"), (None, None))
    expect("truehd7.1 no episode", _episode_hint("Movie.TrueHD.7.1.Atmos.chs.ass"), (None, None))
    files = [
        "待整理/Movies/Oppenheimer.2023.2160p.DDP5.1.Atmos.mkv",
        "待整理/Movies/Friends.S01E01.1080p.mkv",
        "待整理/Movies/Oppenheimer.2023.2160p.DDP5.1.Atmos.chs.ass",
    ]
    matched = match_files(files, files[0], {"title": "Oppenheimer", "year": 2023, "media_type": "movie"})
    expect(
        "audio channel subtitle stays with movie",
        [item.name for item in matched.by_video[norm(files[0])]],
        ["Oppenheimer.2023.2160p.DDP5.1.Atmos.chs.ass"],
    )
    expect("audio channel no unmatched", matched.unmatched, [])

    # ---- 电影整理：字幕、音轨、海报、NFO 一起走，截图留下 ----
    movie_base = "待整理/Movie"
    movie_source = f"{movie_base}/Movie.2023.1080p.mkv"
    source = FakeSource(
        {
            movie_source,
            f"{movie_base}/简体.ass",
            f"{movie_base}/国语.mka",
            f"{movie_base}/poster.jpg",
            f"{movie_base}/movie.nfo",
            f"{movie_base}/字幕截图.png",
        }
    )
    organizer = Organizer(source, {**DEFAULT_NAMING, "auto_category": False}, {"enabled": False})
    parsed = {"title": "Movie", "year": 2023, "resolution": "1080p", "media_type": "movie"}
    match = {"title": "Movie", "year": 2023, "media_type": "movie", "tmdb_id": 1}
    result = organizer.organize(
        source_path=movie_source,
        parsed=parsed,
        match=match,
        target_root="library",
    )
    target_folder = "library/Movie (2023)"
    target_video = f"{target_folder}/Movie (2023) 1080p.mkv"
    expect("movie target", result.target_path, target_video)
    expect("movie sidecar count", (result.sidecars, result.folder_assets), (2, 2))
    expect(
        "movie attached moves",
        set(move for move in source.moves if "Movie (2023)" in move[1]),
        {
            (movie_source, target_video),
            (f"{movie_base}/简体.ass", f"{target_folder}/Movie (2023) 1080p.简体.ass"),
            (f"{movie_base}/国语.mka", f"{target_folder}/Movie (2023) 1080p.国语.mka"),
            (f"{movie_base}/movie.nfo", f"{target_folder}/movie.nfo"),
            (f"{movie_base}/poster.jpg", f"{target_folder}/poster.jpg"),
        },
    )
    expect("movie screenshot stays", f"{movie_base}/字幕截图.png" in source.files, True)

    # ---- 剧集整理：每集字幕跟着走，tvshow.nfo / fanart 放剧集根目录 ----
    show_base = "待整理/Show"
    show_source1 = f"{show_base}/Show.S01E01.1080p.mkv"
    show_source2 = f"{show_base}/Show.S01E02.1080p.mkv"
    source = FakeSource(
        {
            show_source1,
            show_source2,
            f"{show_base}/1.ass",
            f"{show_base}/2.ass",
            f"{show_base}/poster.jpg",
            f"{show_base}/tvshow.nfo",
            f"{show_base}/fanart.jpg",
        }
    )
    organizer = Organizer(source, {**DEFAULT_NAMING, "auto_category": False}, {"enabled": False})
    match = {"title": "Show", "year": 2024, "media_type": "tv", "tmdb_id": 2}
    for episode, source_path in ((1, show_source1), (2, show_source2)):
        parsed = {
            "title": "Show",
            "year": 2024,
            "season": 1,
            "episode": episode,
            "media_type": "tv",
        }
        organizer.organize(
            source_path=source_path,
            parsed=parsed,
            match=match,
            target_root="library",
            season_number=1,
            episode_number=episode,
        )
    season_folder = "library/Show (2024)/Season 1"
    expect(
        "tv episode subtitles",
        [
            move
            for move in source.moves
            if move[0].endswith(("1.ass", "2.ass"))
        ],
        [
            (f"{show_base}/1.ass", f"{season_folder}/Show S01E01.1080p.ass"),
            (f"{show_base}/2.ass", f"{season_folder}/Show S01E02.1080p.ass"),
        ],
    )
    expect("tvshow.nfo at show root", f"library/Show (2024)/tvshow.nfo" in source.files, True)
    expect("fanart at show root", f"library/Show (2024)/fanart.jpg" in source.files, True)
    expect("season poster in season folder", f"{season_folder}/poster.jpg" in source.files, True)

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
