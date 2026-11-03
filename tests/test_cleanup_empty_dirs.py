"""整理后清理空目录的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_cleanup_empty_dirs.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.naming import DEFAULT_NAMING  # noqa: E402
from app.core.organizer import Organizer, remove_empty_source_dirs  # noqa: E402
from app.core.providers.base import SourceError  # noqa: E402
from app.core.providers.local import LocalFileSource  # noqa: E402


class StaleCacheSource(LocalFileSource):
    """模拟网盘：目录已经空了，但缓存仍返回一条旧条目，刷新后才看到真实状态。"""

    def __init__(self, config):
        super().__init__(config)
        self._stale: set[str] = set()

    def mark_stale(self, path: str) -> None:
        self._stale.add(path.replace("\\", "/").strip("/"))

    def refresh(self, path: str) -> bool:
        self._stale.discard(path.replace("\\", "/").strip("/"))
        return True

    def list(self, path: str = ""):
        entries = super().list(path)
        if path.replace("\\", "/").strip("/") in self._stale:
            from app.core.providers.base import Entry

            name = "旧缓存条目.txt"
            entries = [
                *entries,
                Entry(
                    name=name,
                    path=(f"{path.strip('/')}/{name}" if path else name),
                    is_dir=False,
                    size=1,
                ),
            ]
        return entries

MOVIE_PARSED = {"title": "Movie", "year": 2023, "resolution": "1080p", "media_type": "movie"}
MOVIE_MATCH = {"title": "Movie", "year": 2023, "media_type": "movie", "tmdb_id": 1}
MOVIE_SOURCE = "inbox/Movie/Movie.2023.1080p.mkv"
MOVIE_TARGET = "library/Movie (2023)/Movie (2023) 1080p.mkv"


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def write(path: Path, data: bytes = b"data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_organizer(source: LocalFileSource, cleanup: bool) -> Organizer:
    return Organizer(
        source,
        {
            **DEFAULT_NAMING,
            "auto_category": False,
            "cleanup_empty_dirs": cleanup,
        },
        {"enabled": False},
        source_root="inbox",
        refresh_dir=getattr(source, "refresh", None),
    )


def organize_movie(organizer: Organizer, source_path: str = MOVIE_SOURCE):
    return organizer.organize(
        source_path=source_path,
        parsed=MOVIE_PARSED,
        match=MOVIE_MATCH,
        target_root="library",
    )


def movie_case(base: Path, cleanup: bool, with_keep_file: bool = False):
    write(base / MOVIE_SOURCE)
    write(base / "inbox/Movie/Subs/简体.ass")
    if with_keep_file:
        write(base / "inbox/Movie/keep.txt")
    source = LocalFileSource({"base_path": str(base)})
    result = organize_movie(make_organizer(source, cleanup))
    return base, result


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    # ---- 开启清理：视频与子目录字幕移走后，Subs / 作品目录依次删除，源根保留 ----
    with tempfile.TemporaryDirectory(prefix="cinemint-cleanup-") as temp:
        base, result = movie_case(Path(temp), cleanup=True)
        expect("cleanup movie target", (base / MOVIE_TARGET).is_file(), True)
        expect("cleanup video removed", (base / MOVIE_SOURCE).exists(), False)
        expect("cleanup subtitle removed", (base / "inbox/Movie/Subs/简体.ass").exists(), False)
        expect("cleanup subs dir removed", (base / "inbox/Movie/Subs").exists(), False)
        expect("cleanup movie dir removed", (base / "inbox/Movie").exists(), False)
        expect("cleanup source root kept", (base / "inbox").is_dir(), True)
        expect(
            "cleanup removed dirs",
            set(result.removed_dirs or []),
            {"inbox/Movie/Subs", "inbox/Movie"},
        )

    # ---- 关闭清理：文件和目录只移动，不删除源目录 ----
    with tempfile.TemporaryDirectory(prefix="cinemint-cleanup-") as temp:
        base, result = movie_case(Path(temp), cleanup=False)
        expect("no cleanup target", (base / MOVIE_TARGET).is_file(), True)
        expect("no cleanup source dir", (base / "inbox/Movie").is_dir(), True)
        expect("no cleanup subs dir", (base / "inbox/Movie/Subs").is_dir(), True)
        expect("no cleanup removed dirs", result.removed_dirs, None)

    # ---- 非空目录不删除，但已经清空的字幕子目录仍可删除 ----
    with tempfile.TemporaryDirectory(prefix="cinemint-cleanup-") as temp:
        base, result = movie_case(Path(temp), cleanup=True, with_keep_file=True)
        expect("non-empty movie dir kept", (base / "inbox/Movie").is_dir(), True)
        expect("non-empty keep file kept", (base / "inbox/Movie/keep.txt").is_file(), True)
        expect("non-empty subs dir removed", (base / "inbox/Movie/Subs").exists(), False)
        expect("non-empty removed dirs", result.removed_dirs, ["inbox/Movie/Subs"])

    # ---- 视频直接在源根目录：只整理文件，不删除源根 ----
    with tempfile.TemporaryDirectory(prefix="cinemint-cleanup-") as temp:
        base = Path(temp)
        root_source = "inbox/Movie.2023.1080p.mkv"
        write(base / root_source)
        source = LocalFileSource({"base_path": str(base)})
        result = organize_movie(make_organizer(source, cleanup=True), root_source)
        expect("root file target", (base / MOVIE_TARGET).is_file(), True)
        expect("root file source root kept", (base / "inbox").is_dir(), True)
        expect("root file no removed dirs", result.removed_dirs, None)

    # ---- 符号链接目录不删除，也不进入链接目标清理 ----
    with tempfile.TemporaryDirectory(prefix="cinemint-cleanup-") as temp:
        base = Path(temp)
        outside = base / "outside"
        outside.mkdir()
        (base / "inbox").mkdir()
        link = base / "inbox/link"
        os.symlink(outside, link, target_is_directory=True)
        source = LocalFileSource({"base_path": str(base)})
        removed, warnings = remove_empty_source_dirs(
            source,
            ["inbox/link/Movie.2023.1080p.mkv"],
            "inbox",
        )
        expect("symlink not removed", removed, [])
        expect("symlink no warnings", warnings, [])
        expect("symlink remains", link.is_symlink(), True)
        expect("symlink target remains", outside.is_dir(), True)

    # ---- 目标目录位于源根内时不被清理器触碰 ----
    with tempfile.TemporaryDirectory(prefix="cinemint-cleanup-") as temp:
        base = Path(temp)
        source = LocalFileSource({"base_path": str(base)})
        (base / "inbox/library/Show").mkdir(parents=True)
        removed, warnings = remove_empty_source_dirs(
            source,
            ["inbox/library/Show/Show.S01E01.mkv"],
            "inbox",
            "inbox/library",
        )
        expect("target guard removed dirs", removed, [])
        expect("target guard warnings", warnings, [])
        expect("target guard target kept", (base / "inbox/library/Show").is_dir(), True)

    # ---- 本地文件源的空目录删除是非递归的，非空目录必须拒绝 ----
    with tempfile.TemporaryDirectory(prefix="cinemint-cleanup-") as temp:
        base = Path(temp)
        source = LocalFileSource({"base_path": str(base)})
        (base / "inbox/Empty").mkdir(parents=True)
        (base / "inbox/Full").mkdir()
        write(base / "inbox/Full/keep.txt")
        source.remove_empty_dir("inbox/Empty")
        expect("provider empty dir removed", (base / "inbox/Empty").exists(), False)
        try:
            source.remove_empty_dir("inbox/Full")
        except SourceError:
            refused = True
        else:
            refused = False
        expect("provider non-empty refused", refused, True)
        expect("provider non-empty kept", (base / "inbox/Full/keep.txt").is_file(), True)

    # ---- 网盘目录缓存滞后：刷新后确认已空，仍会删除；不刷新则保留 ----
    with tempfile.TemporaryDirectory(prefix="cinemint-cleanup-") as temp:
        base = Path(temp)
        write(base / MOVIE_SOURCE)
        write(base / "inbox/Movie/Subs/简体.ass")
        source = StaleCacheSource({"base_path": str(base)})
        organizer = make_organizer(source, True)
        source.mark_stale("inbox/Movie")
        source.mark_stale("inbox/Movie/Subs")
        result = organize_movie(organizer)
        expect("stale cache target", (base / MOVIE_TARGET).is_file(), True)
        expect("stale cache subs removed", (base / "inbox/Movie/Subs").exists(), False)
        expect("stale cache movie removed", (base / "inbox/Movie").exists(), False)
        expect(
            "stale cache removed dirs",
            set(result.removed_dirs or []),
            {"inbox/Movie/Subs", "inbox/Movie"},
        )

    # ---- 没有刷新回调时，缓存滞后按非空处理，不误删 ----
    with tempfile.TemporaryDirectory(prefix="cinemint-cleanup-") as temp:
        base = Path(temp)
        write(base / MOVIE_SOURCE)
        source = StaleCacheSource({"base_path": str(base)})
        organizer = Organizer(
            source,
            {**DEFAULT_NAMING, "auto_category": False, "cleanup_empty_dirs": True},
            {"enabled": False},
            source_root="inbox",
        )
        source.mark_stale("inbox/Movie")
        result = organize_movie(organizer)
        expect("no refresh target", (base / MOVIE_TARGET).is_file(), True)
        expect("no refresh movie kept", (base / "inbox/Movie").is_dir(), True)
        expect("no refresh removed dirs", result.removed_dirs, None)

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
