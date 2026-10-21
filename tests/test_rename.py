"""仅重命名（不移动）的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_rename.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.naming import DEFAULT_NAMING  # noqa: E402
from app.core.providers.local import LocalFileSource  # noqa: E402
from app.core.renamer import (  # noqa: E402
    apply_folder_step,
    build_rename_plan,
    collect_folder_steps,
    rename_file_in_place,
)


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


MOVIE_MATCH = {
    "tmdb_id": 1,
    "media_type": "movie",
    "title": "红毯先生",
    "year": 2024,
}
MOVIE_PARSED = {"year": 2024, "raw": "红毯先生.mkv"}
TV_MATCH = {
    "tmdb_id": 2,
    "media_type": "tv",
    "title": "老友记",
    "year": 1994,
}
TV_PARSED = {"season": 1, "episode": 1, "raw": "老友记S01E01.mkv"}


def make_source(root: Path, files: list[str]) -> LocalFileSource:
    for relative in files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
    return LocalFileSource({"base_path": str(root)})


def movie_item(path: str, status: str = "matched") -> dict:
    return {
        "id": 1,
        "source_path": path,
        "file_name": path.rsplit("/", 1)[-1],
        "status": status,
        "match_key": "movie:1",
    }


def tv_item(item_id: int, path: str) -> dict:
    return {
        "id": item_id,
        "source_path": path,
        "file_name": path.rsplit("/", 1)[-1],
        "status": "matched",
        "match_key": "tv:2",
    }


def plan(source, item, match, parsed, *, files=True, folders=True, root="", guard=None):
    return build_rename_plan(
        source=source,
        naming=DEFAULT_NAMING,
        item=item,
        parsed=parsed,
        match=match,
        rename_files=files,
        rename_folders=folders,
        root_path=root,
        folder_guard=guard,
    )


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    # ---- 电影：多级父目录只改最后一层，文件按模板改名 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["a/b/c/d/红毯先生.mkv"])
        next_plan = plan(
            source,
            movie_item("a/b/c/d/红毯先生.mkv"),
            MOVIE_MATCH,
            MOVIE_PARSED,
            root="a",
        )
        expect(
            "movie both target",
            next_plan.target_path,
            "a/b/c/红毯先生 (2024)/红毯先生 (2024).mkv",
        )
        expect("movie both folders", [s.old_path for s in next_plan.folder_steps], ["a/b/c/d"])
        expect("movie both file changed", next_plan.file_changed, True)
        expect("movie both folder changed", next_plan.folder_changed, True)

        for step in collect_folder_steps([next_plan]):
            apply_folder_step(source, step)
        result = rename_file_in_place(
            source=source,
            naming=DEFAULT_NAMING,
            source_path="a/b/c/红毯先生 (2024)/红毯先生.mkv",
            parsed=MOVIE_PARSED,
            desired_name=next_plan.desired_file_name,
        )
        expect(
            "movie both applied",
            source.exists("a/b/c/红毯先生 (2024)/红毯先生 (2024).mkv"),
            True,
        )
        expect("movie both result", result.target_path, "a/b/c/红毯先生 (2024)/红毯先生 (2024).mkv")

    # ---- 仅文件 / 仅文件夹 / 都不选 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["a/b/c/d/红毯先生.mkv"])
        only_file = plan(
            source,
            movie_item("a/b/c/d/红毯先生.mkv"),
            MOVIE_MATCH,
            MOVIE_PARSED,
            files=True,
            folders=False,
            root="a",
        )
        expect("file only target", only_file.target_path, "a/b/c/d/红毯先生 (2024).mkv")
        expect("file only no folder", only_file.folder_steps, [])

        only_folder = plan(
            source,
            movie_item("a/b/c/d/红毯先生.mkv"),
            MOVIE_MATCH,
            MOVIE_PARSED,
            files=False,
            folders=True,
            root="a",
        )
        expect("folder only target", only_folder.target_path, "a/b/c/红毯先生 (2024)/红毯先生.mkv")
        expect("folder only file unchanged", only_folder.file_changed, False)
        expect("folder only folder changed", only_folder.folder_changed, True)

        neither = plan(
            source,
            movie_item("a/b/c/d/红毯先生.mkv"),
            MOVIE_MATCH,
            MOVIE_PARSED,
            files=False,
            folders=False,
            root="a",
        )
        expect("neither unchanged", neither.changed, False)
        expect("neither target", neither.target_path, "a/b/c/d/红毯先生.mkv")

    # ---- 剧集：剧名目录 + 季目录都要改，文件改名 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["inbox/喜剧之王/abc/老友记S01E01.mkv"])
        tv = plan(
            source,
            tv_item(1, "inbox/喜剧之王/abc/老友记S01E01.mkv"),
            TV_MATCH,
            TV_PARSED,
            root="inbox",
        )
        expect(
            "tv both target",
            tv.target_path,
            "inbox/老友记 (1994)/Season 1/老友记 S01E01.mkv",
        )
        expect("tv folder levels", len(tv.folder_steps), 2)
        expect(
            "tv season step in place",
            [s.new_path for s in tv.folder_steps if s.old_path.endswith("/abc")],
            ["inbox/喜剧之王/Season 1"],
        )

        for step in collect_folder_steps([tv]):
            apply_folder_step(source, step)
        expect("tv folders applied", source.exists("inbox/老友记 (1994)/Season 1/老友记S01E01.mkv"), True)
        rename_file_in_place(
            source=source,
            naming=DEFAULT_NAMING,
            source_path="inbox/老友记 (1994)/Season 1/老友记S01E01.mkv",
            parsed=TV_PARSED,
            desired_name=tv.desired_file_name,
        )
        expect(
            "tv both applied",
            source.exists("inbox/老友记 (1994)/Season 1/老友记 S01E01.mkv"),
            True,
        )

    # ---- 剧集目录层级不足：只改文件，不改文件夹 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["inbox/abc/ShowS01E01.mkv"])
        tv = plan(
            source,
            tv_item(1, "inbox/abc/ShowS01E01.mkv"),
            {**TV_MATCH, "title": "Show", "year": 2020},
            {**TV_PARSED, "raw": "ShowS01E01.mkv"},
            root="inbox",
        )
        expect("tv shallow file target", tv.target_path, "inbox/abc/Show S01E01.mkv")
        expect("tv shallow no folder steps", tv.folder_steps, [])
        expect("tv shallow warning", any("目录层级" in warning for warning in tv.warnings), True)

    # ---- 电影直接在库根目录：不改根目录名 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["inbox/红毯先生.mkv"])
        movie = plan(
            source,
            movie_item("inbox/红毯先生.mkv"),
            MOVIE_MATCH,
            MOVIE_PARSED,
            root="inbox",
        )
        expect("movie root file target", movie.target_path, "inbox/红毯先生 (2024).mkv")
        expect("movie root no folder", movie.folder_changed, False)
        expect("movie root warning", any("目录层级" in warning for warning in movie.warnings), True)

    # ---- 视频位于 Subs / 花絮子目录：不改上层作品目录 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["inbox/show/Season 1/Subs/老友记S01E01.mkv"])
        tv = plan(
            source,
            tv_item(1, "inbox/show/Season 1/Subs/老友记S01E01.mkv"),
            TV_MATCH,
            TV_PARSED,
            root="inbox",
        )
        expect("subs no folder", tv.folder_changed, False)
        expect("subs warning", any("字幕" in warning for warning in tv.warnings), True)
        expect("subs file target", tv.target_path, "inbox/show/Season 1/Subs/老友记 S01E01.mkv")

    # ---- 目标文件夹已存在：跳过文件夹改名 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["a/d/红毯先生.mkv", "a/红毯先生 (2024)/keep.txt"])
        movie = plan(
            source,
            movie_item("a/d/红毯先生.mkv"),
            MOVIE_MATCH,
            MOVIE_PARSED,
            root="a",
        )
        expect("folder exists skip", movie.folder_changed, False)
        expect("folder exists warning", any("目标文件夹已存在" in warning for warning in movie.warnings), True)
        expect("folder exists file still", movie.file_changed, True)
        expect("folder exists target", movie.target_path, "a/d/红毯先生 (2024).mkv")

    # ---- 目录混有其他作品：文件夹改名被保护 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["a/d/红毯先生.mkv"])
        movie = plan(
            source,
            movie_item("a/d/红毯先生.mkv"),
            MOVIE_MATCH,
            MOVIE_PARSED,
            root="a",
            guard=lambda folder: False,
        )
        expect("guard blocks folder", movie.folder_changed, False)
        expect("guard warning", any("其他作品" in warning for warning in movie.warnings), True)

    # ---- 同名冲突：沿用 (1) 规则 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["a/d/红毯先生.mkv", "a/d/红毯先生 (2024).mkv"])
        movie = plan(
            source,
            movie_item("a/d/红毯先生.mkv"),
            MOVIE_MATCH,
            MOVIE_PARSED,
            folders=False,
            root="a",
        )
        expect("file collision", movie.target_path, "a/d/红毯先生 (2024) (1).mkv")

    # ---- 附带字幕跟随文件改名 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["a/d/红毯先生.mkv", "a/d/红毯先生.chs.ass"])
        result = rename_file_in_place(
            source=source,
            naming=DEFAULT_NAMING,
            source_path="a/d/红毯先生.mkv",
            parsed=MOVIE_PARSED,
            desired_name="红毯先生 (2024).mkv",
        )
        expect("sidecar moved", source.exists("a/d/红毯先生 (2024).chs.ass"), True)
        expect("sidecar count", result.moved_sidecars, 1)

    # ---- 剧集多集共用文件夹只改一次 ----
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = make_source(root, ["inbox/abc/S01/Show.S01E01.mkv", "inbox/abc/S01/Show.S01E02.mkv"])
        items = [
            tv_item(
                1,
                "inbox/abc/S01/Show.S01E01.mkv",
            ),
            tv_item(
                2,
                "inbox/abc/S01/Show.S01E02.mkv",
            ),
        ]
        parsed = [{**TV_PARSED, "episode": 1, "raw": "Show.S01E01.mkv"}, {**TV_PARSED, "episode": 2, "raw": "Show.S01E02.mkv"}]
        match = {**TV_MATCH, "title": "Show", "year": 2020}
        plans = [
            plan(source, items[0], match, parsed[0], root="inbox", guard=lambda folder: True),
            plan(source, items[1], match, parsed[1], root="inbox", guard=lambda folder: True),
        ]
        steps = collect_folder_steps(plans)
        expect("shared folder steps", len(steps), 2)
        for step in steps:
            apply_folder_step(source, step)
        expect("shared folder show", source.exists("inbox/Show (2020)/Season 1/Show.S01E01.mkv"), True)
        expect("shared folder show 2", source.exists("inbox/Show (2020)/Season 1/Show.S01E02.mkv"), True)
        for item, item_parsed in zip(items, parsed):
            rename_file_in_place(
                source=source,
                naming=DEFAULT_NAMING,
                source_path=f"inbox/Show (2020)/Season 1/{item['file_name']}",
                parsed=item_parsed,
                desired_name=f"Show S01E0{item['id']}.mkv",
            )
        expect("shared files renamed", source.exists("inbox/Show (2020)/Season 1/Show S01E02.mkv"), True)

    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
