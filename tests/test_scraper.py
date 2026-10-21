"""NFO 命名与刮削输出回归测试。

运行：cd backend && .venv/bin/python ../tests/test_scraper.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.naming import DEFAULT_NAMING  # noqa: E402
from app.core.organizer import Organizer  # noqa: E402
from app.core.providers.base import Entry, FileSource, norm  # noqa: E402


class FakeSource(FileSource):
    type = "fake"

    def __init__(self, files: set[str]):
        super().__init__({})
        self.files = {norm(path) for path in files}
        self.data: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.moves: list[tuple[str, str]] = []

    def list(self, path: str = "") -> list[Entry]:
        parent = norm(path)
        entries = []
        for file_path in sorted(self.files):
            file_parent, _, name = file_path.rpartition("/")
            if file_parent == parent:
                entries.append(
                    Entry(
                        name=name,
                        path=file_path,
                        is_dir=False,
                        size=len(self.data.get(file_path, b"")),
                    )
                )
        for dir_path in sorted(self.dirs):
            dir_parent, _, name = dir_path.rpartition("/")
            if dir_parent == parent:
                entries.append(Entry(name=name, path=dir_path, is_dir=True))
        return entries

    def exists(self, path: str) -> bool:
        return norm(path) in self.files or norm(path) in self.dirs

    def mkdir(self, path: str) -> None:
        current = norm(path)
        while current:
            self.dirs.add(current)
            current = current.rpartition("/")[0]

    def read(self, path: str) -> bytes:
        return self.data[norm(path)]

    def write(self, path: str, data: bytes) -> None:
        path = norm(path)
        self.files.add(path)
        self.data[path] = data

    def move(self, src: str, dst: str) -> None:
        src, dst = norm(src), norm(dst)
        self.moves.append((src, dst))
        self.files.discard(src)
        self.files.add(dst)
        if src in self.data:
            self.data[dst] = self.data.pop(src)


class FakeImages:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def fetch(self, path: str, size: str = "w780", title: str = "", subtitle: str = "") -> bytes | None:
        if not path:
            return None
        self.calls.append((path, size))
        if "logo" in path:
            return b"logo"
        if "season" in path:
            return b"season-poster"
        if "still" in path:
            return b"episode-thumb"
        if "backdrop" in path:
            return b"fanart"
        return b"poster"


def make_organizer(source: FakeSource, images: FakeImages, scrape: dict | None = None) -> Organizer:
    config = {
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
    if scrape:
        config.update(scrape)
    return Organizer(
        source,
        {**DEFAULT_NAMING, "auto_category": False},
        config,
        images=images,
    )


MOVIE_MATCH = {
    "title": "Movie",
    "original_title": "Movie",
    "year": 2023,
    "media_type": "movie",
    "tmdb_id": 1,
    "poster_path": "/poster.jpg",
    "backdrop_path": "/backdrop.jpg",
    "logo_path": "/logo.png",
    "overview": "剧情",
}
MOVIE_DETAILS = {
    **MOVIE_MATCH,
    "runtime": 120,
    "vote_average": 7.5,
    "vote_count": 100,
    "genres": ["剧情"],
}

TV_MATCH = {
    "title": "Show",
    "original_title": "Show",
    "year": 2024,
    "media_type": "tv",
    "tmdb_id": 2,
    "poster_path": "/poster.jpg",
    "backdrop_path": "/backdrop.jpg",
    "logo_path": "/logo.png",
    "overview": "剧集简介",
}
TV_DETAILS = {
    **TV_MATCH,
    "genres": ["剧情"],
}


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    # ---- 电影：NFO 与最终视频同名，不再生成 movie.nfo ----
    movie_source = "待整理/Movie/Movie.2023.1080p.mkv"
    source = FakeSource({movie_source})
    images = FakeImages()
    organizer = make_organizer(source, images)
    show_folder = organizer.show_folder_for(MOVIE_MATCH, "library", details=MOVIE_DETAILS)
    organizer.scrape_show(show_folder, MOVIE_MATCH, MOVIE_DETAILS, {})
    result = organizer.organize(
        source_path=movie_source,
        parsed={"title": "Movie", "year": 2023, "resolution": "1080p", "media_type": "movie"},
        match=MOVIE_MATCH,
        target_root="library",
        details=MOVIE_DETAILS,
    )
    movie_target = "library/Movie (2023)/Movie (2023) 1080p.mkv"
    movie_nfo = "library/Movie (2023)/Movie (2023) 1080p.nfo"
    expect("movie target", result.target_path, movie_target)
    expect("movie same-name nfo", movie_nfo in source.files, True)
    expect("movie generic nfo absent", "library/Movie (2023)/movie.nfo" in source.files, False)
    expect("movie nfo content", "<movie>" in source.read(movie_nfo).decode("utf-8"), True)
    expect("movie poster", source.exists("library/Movie (2023)/poster.jpg"), True)
    expect("movie fanart", source.exists("library/Movie (2023)/fanart.jpg"), True)
    expect("movie logo", source.exists("library/Movie (2023)/logo.png"), True)
    expect("movie folder image", source.exists("library/Movie (2023)/folder.jpg"), True)

    # ---- 电影重名：NFO 跟随最终视频的 (1) 名称 ----
    movie_source = "待整理/Movie/Movie.2023.1080p.mkv"
    source = FakeSource(
        {
            movie_source,
            "library/Movie (2023)/Movie (2023) 1080p.mkv",
        }
    )
    organizer = make_organizer(source, FakeImages())
    result = organizer.organize(
        source_path=movie_source,
        parsed={"title": "Movie", "year": 2023, "resolution": "1080p", "media_type": "movie"},
        match=MOVIE_MATCH,
        target_root="library",
        details=MOVIE_DETAILS,
    )
    expect("movie collision target", result.target_path, "library/Movie (2023)/Movie (2023) 1080p (1).mkv")
    expect(
        "movie collision nfo",
        "library/Movie (2023)/Movie (2023) 1080p (1).nfo" in source.files,
        True,
    )
    expect(
        "movie collision base nfo absent",
        "library/Movie (2023)/Movie (2023) 1080p.nfo" in source.files,
        False,
    )

    # ---- 剧集：tvshow.nfo / season.nfo 固定名；单集 NFO 与视频同名 ----
    show_source = "待整理/Show/Show.S01E01.1080p.mkv"
    source = FakeSource({show_source})
    images = FakeImages()
    organizer = make_organizer(source, images)
    show_folder = organizer.show_folder_for(TV_MATCH, "library", details=TV_DETAILS)
    organizer.scrape_show(
        show_folder,
        TV_MATCH,
        TV_DETAILS,
        {1: {"name": "第 1 季", "overview": "第一季", "air_date": "2024-01-01", "poster_path": "/season.jpg"}},
    )
    result = organizer.organize(
        source_path=show_source,
        parsed={"title": "Show", "year": 2024, "season": 1, "episode": 1, "media_type": "tv"},
        match=TV_MATCH,
        target_root="library",
        episode={"name": "第一集", "overview": "剧情", "still_path": "/still.jpg"},
        season_number=1,
        episode_number=1,
        details=TV_DETAILS,
    )
    episode_target = "library/Show (2024)/Season 1/Show S01E01.1080p.mkv"
    episode_nfo = "library/Show (2024)/Season 1/Show S01E01.1080p.nfo"
    expect("tv episode target", result.target_path, episode_target)
    expect("tvshow.nfo fixed name", source.exists("library/Show (2024)/tvshow.nfo"), True)
    expect("season.nfo fixed name", source.exists("library/Show (2024)/Season 1/season.nfo"), True)
    expect("tvshow.nfo content", "<tvshow>" in source.read("library/Show (2024)/tvshow.nfo").decode("utf-8"), True)
    expect(
        "season.nfo content",
        "<season>" in source.read("library/Show (2024)/Season 1/season.nfo").decode("utf-8"),
        True,
    )
    expect("episode same-name nfo", episode_nfo in source.files, True)
    expect("episode nfo content", "<episodedetails>" in source.read(episode_nfo).decode("utf-8"), True)
    expect("season poster", source.exists("library/Show (2024)/Season 1/poster.jpg"), True)
    expect("episode thumb", source.exists("library/Show (2024)/Season 1/Show S01E01.1080p-thumb.jpg"), True)
    expect("no movie.nfo", "library/Show (2024)/movie.nfo" in source.files, False)

    # ---- 关闭刮削：同名 NFO 和图片都不生成 ----
    movie_source = "待整理/Movie/Movie.2023.1080p.mkv"
    source = FakeSource({movie_source})
    organizer = make_organizer(source, FakeImages(), {"enabled": False})
    result = organizer.organize(
        source_path=movie_source,
        parsed={"title": "Movie", "year": 2023, "resolution": "1080p", "media_type": "movie"},
        match=MOVIE_MATCH,
        target_root="library",
        details=MOVIE_DETAILS,
    )
    expect("disabled movie target", result.target_path, "library/Movie (2023)/Movie (2023) 1080p.mkv")
    expect("disabled nfo absent", "library/Movie (2023)/Movie (2023) 1080p.nfo" in source.files, False)
    expect("disabled poster absent", "library/Movie (2023)/poster.jpg" in source.files, False)

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
