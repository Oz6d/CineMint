"""附件子目录扫描 / 手动关联 / 整理后路径迁移的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_attachment_subdirs.py
脚本使用临时数据目录和临时本地媒体，不会写 data/cineflow.db。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cineflow-attachment-subdirs-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="cineflow-attachment-media-")
os.environ["CINEFLOW_DATA_DIR"] = TEMP_DATA
os.environ["CINEFLOW_DEMO"] = "false"
os.environ["CINEFLOW_ADMIN_PASSWORD"] = "cineflow"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.attachment_links import load_attachment_links  # noqa: E402
from app.core.attachments import (  # noqa: E402
    is_attachment_directory,
    load_directory_attachments,
)
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402
from app.core.organizer import Organizer  # noqa: E402
from app.core.providers.base import norm  # noqa: E402
from app.core.providers.local import LocalFileSource  # noqa: E402
from app.core.runner import _remap_attachment_paths  # noqa: E402
from app.db import dumps, execute, now, query_one  # noqa: E402
from app.main import app  # noqa: E402


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def add_item(library_id: int, source_path: str, name: str, parsed: dict, match: dict) -> int:
    return execute(
        "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, status, "
        "match_key, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            library_id,
            source_path,
            name,
            8,
            0,
            dumps(parsed),
            dumps(match),
            "matched",
            f"{match['media_type']}:{match['tmdb_id']}",
            now(),
            now(),
        ),
    )


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    media = Path(MEDIA_ROOT)
    movie_dir = media / "inbox" / "Movie"
    movie_subs = movie_dir / "Subs"
    movie_subs.mkdir(parents=True)
    movie_source = "inbox/Movie/Movie.2023.1080p.mkv"
    (movie_dir / "Movie.2023.1080p.mkv").write_bytes(b"movie")
    (movie_subs / "简体.ass").write_text("subtitle")
    (movie_subs / "国语.mka").write_bytes(b"audio")
    (movie_subs / "poster.jpg").write_bytes(b"poster")
    (movie_subs / "movie.nfo").write_text("<movie />")

    show_dir = media / "inbox" / "Show"
    show_subs = show_dir / "Subs"
    show_other = show_dir / "Other"
    show_subs.mkdir(parents=True)
    show_other.mkdir(parents=True)
    show_source1 = "inbox/Show/Show.S01E01.1080p.mkv"
    show_source2 = "inbox/Show/Show.S01E02.1080p.mkv"
    (show_dir / "Show.S01E01.1080p.mkv").write_bytes(b"episode1")
    (show_dir / "Show.S01E02.1080p.mkv").write_bytes(b"episode2")
    (show_subs / "Show.S01E01.chs.ass").write_text("episode 1")
    (show_subs / "Show.S01E02.chs.ass").write_text("episode 2")
    (show_subs / "简体.ass").write_text("ambiguous")
    (show_subs / "poster.jpg").write_bytes(b"poster")
    (show_other / "简体.ass").write_text("not a known subdir")
    (show_dir / "notes.txt").write_text("not a video")

    movie_parsed = {
        "title": "Movie",
        "year": 2023,
        "media_type": "movie",
        "raw": "Movie.2023.1080p.mkv",
    }
    movie_match = {"tmdb_id": 11, "media_type": "movie", "title": "Movie", "year": 2023}
    show_match = {"tmdb_id": 22, "media_type": "tv", "title": "Show", "year": 2024}
    show_parsed1 = {
        "title": "Show",
        "year": 2024,
        "season": 1,
        "episode": 1,
        "media_type": "tv",
        "raw": "Show.S01E01.1080p.mkv",
    }
    show_parsed2 = {
        "title": "Show",
        "year": 2024,
        "season": 1,
        "episode": 2,
        "media_type": "tv",
        "raw": "Show.S01E02.1080p.mkv",
    }

    source = LocalFileSource({"base_path": str(media)})
    matched = load_directory_attachments(source, movie_source, movie_parsed)
    expect(
        "subdir subtitles and audio picked",
        sorted(item.name for item in matched.by_video[norm(movie_source)]),
        ["国语.mka", "简体.ass"],
    )
    expect(
        "subdir poster and nfo ignored",
        [item.name for item in matched.folder_assets],
        [],
    )

    matched = load_directory_attachments(source, show_source1, show_parsed1)
    expect(
        "episode subtitle from subdir",
        [item.name for item in matched.by_video[norm(show_source1)]],
        ["Show.S01E01.chs.ass"],
    )
    expect(
        "other episode subtitle from subdir",
        [item.name for item in matched.by_video[norm(show_source2)]],
        ["Show.S01E02.chs.ass"],
    )
    expect(
        "ambiguous subdir subtitle unmatched",
        [item.name for item in matched.unmatched],
        ["简体.ass"],
    )

    expect(
        "same dir allowed",
        is_attachment_directory("inbox/Show", "inbox/Show"),
        True,
    )
    expect(
        "known subdir allowed",
        is_attachment_directory("inbox/Show", "inbox/Show/Subs"),
        True,
    )
    expect(
        "known subdir case insensitive",
        is_attachment_directory("inbox/Show", "inbox/Show/subs"),
        True,
    )
    expect(
        "unknown subdir rejected",
        is_attachment_directory("inbox/Show", "inbox/Show/Other"),
        False,
    )
    expect(
        "nested subdir rejected",
        is_attachment_directory("inbox/Show", "inbox/Show/Subs/Nested"),
        False,
    )

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cineflow"})
        expect("login", login.status_code, 200)

        source_id = execute(
            "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("临时本地源", "local", dumps({"base_path": str(media)}), now(), now()),
        )
        library_id = execute(
            "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
            "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "附件子目录测试库",
                "inbox",
                "library",
                "auto",
                dumps({**DEFAULT_NAMING, "auto_category": False}),
                dumps({**DEFAULT_SCRAPE, "enabled": False}),
                1,
                0,
                now(),
                now(),
            ),
        )
        add_item(library_id, movie_source, "Movie.2023.1080p.mkv", movie_parsed, movie_match)
        first_id = add_item(library_id, show_source1, "Show.S01E01.1080p.mkv", show_parsed1, show_match)
        second_id = add_item(library_id, show_source2, "Show.S01E02.1080p.mkv", show_parsed2, show_match)

        link = client.post(
            f"/api/items/{first_id}/attachments/link",
            json={"path": "inbox/Show/Subs/简体.ass"},
        )
        expect("link subdir status", link.status_code, 200)
        expect(
            "link subdir manual badge",
            next(
                (item.get("manual") for item in link.json().get("items", []) if item.get("name") == "简体.ass"),
                None,
            ),
            True,
        )

        link_other = client.post(
            f"/api/items/{first_id}/attachments/link",
            json={"path": "inbox/Show/Other/简体.ass"},
        )
        expect("link unknown subdir rejected", link_other.status_code, 400)

        link_poster = client.post(
            f"/api/items/{first_id}/attachments/link",
            json={"path": "inbox/Show/Subs/poster.jpg"},
        )
        expect("link subdir poster rejected", link_poster.status_code, 400)

        unlink = client.post(
            f"/api/items/{first_id}/attachments/unlink",
            json={"path": "inbox/Show/Subs/简体.ass"},
        )
        expect("unlink subdir status", unlink.status_code, 200)

        relink = client.post(
            f"/api/items/{first_id}/attachments/link",
            json={"path": "inbox/Show/Subs/简体.ass"},
        )
        expect("relink subdir status", relink.status_code, 200)

        payload = client.get(f"/api/items/{first_id}/attachments").json()
        expect("payload current path", payload.get("current_path"), show_source1)
        expect(
            "payload sibling videos",
            sorted(video.get("path") for video in payload.get("videos") or []),
            [show_source1, show_source2],
        )

        link_target = client.post(
            f"/api/items/{first_id}/attachments/link",
            json={
                "path": "inbox/Show/Subs/简体.ass",
                "target_video_path": show_source2,
            },
        )
        expect("link target episode status", link_target.status_code, 200)
        second_payload = client.get(f"/api/items/{second_id}/attachments").json()
        expect(
            "link target episode manual badge",
            next(
                (
                    item.get("manual")
                    for item in second_payload.get("items", [])
                    if item.get("name") == "简体.ass"
                ),
                None,
            ),
            True,
        )

        invalid_target_crossdir = client.post(
            f"/api/items/{first_id}/attachments/link",
            json={
                "path": "inbox/Show/Subs/简体.ass",
                "target_video_path": "inbox/Movie/Movie.2023.1080p.mkv",
            },
        )
        expect("link target cross dir rejected", invalid_target_crossdir.status_code, 400)

        invalid_target_nonvideo = client.post(
            f"/api/items/{first_id}/attachments/link",
            json={
                "path": "inbox/Show/Subs/简体.ass",
                "target_video_path": "inbox/Show/notes.txt",
            },
        )
        expect("link target non video rejected", invalid_target_nonvideo.status_code, 400)

        relink_current = client.post(
            f"/api/items/{first_id}/attachments/link",
            json={
                "path": "inbox/Show/Subs/简体.ass",
                "target_video_path": show_source1,
            },
        )
        expect("relink current episode status", relink_current.status_code, 200)

        manual_links = load_attachment_links(library_id)
        organizer = Organizer(
            source,
            {**DEFAULT_NAMING, "auto_category": False},
            {"enabled": False},
            manual_links=manual_links,
        )
        result = organizer.organize(
            source_path=show_source1,
            parsed=show_parsed1,
            match=show_match,
            target_root="library",
            season_number=1,
            episode_number=1,
        )
        season_folder = "library/Show (2024)/Season 1"
        target_video = f"{season_folder}/Show S01E01.1080p.mkv"
        target_episode_sub = f"{season_folder}/Show S01E01.1080p.chs.ass"
        target_manual_sub = f"{season_folder}/Show S01E01.1080p.简体.ass"
        expect("organize target", result.target_path, target_video)
        expect(
            "organizer reports video move",
            (result.moved_paths or {}).get(show_source1),
            target_video,
        )
        expect(
            "organizer moves episode subtitle from subdir",
            (result.moved_paths or {}).get("inbox/Show/Subs/Show.S01E01.chs.ass"),
            target_episode_sub,
        )
        expect(
            "organizer moves manual subtitle from subdir",
            (result.moved_paths or {}).get("inbox/Show/Subs/简体.ass"),
            target_manual_sub,
        )
        expect("subdir episode subtitle exists at target", (media / target_episode_sub).exists(), True)
        expect("subdir manual subtitle exists at target", (media / target_manual_sub).exists(), True)
        expect(
            "other episode subtitle stays",
            (media / "inbox/Show/Subs/Show.S01E02.chs.ass").exists(),
            True,
        )

        _remap_attachment_paths(
            library_id,
            lambda path, mapping=result.moved_paths or {}: mapping.get(norm(path), norm(path)),
        )
        migrated = query_one(
            "SELECT video_path, attachment_path FROM attachment_links "
            "WHERE library_id=? AND attachment_path=?",
            (library_id, target_manual_sub),
        )
        expect(
            "manual link video migrated",
            migrated["video_path"] if migrated else "",
            target_video,
        )
        expect(
            "manual link attachment migrated",
            migrated["attachment_path"] if migrated else "",
            target_manual_sub,
        )
        expect(
            "manual link loaded from target",
            load_attachment_links(library_id).get(target_manual_sub),
            target_video,
        )

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
