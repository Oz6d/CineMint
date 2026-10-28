"""「所有文件」列表 / 匹配 / 删除 / 移动 API 回归测试。

运行：cd backend && .venv/bin/python ../tests/test_files_api.py
脚本内部使用临时数据目录和临时媒体目录，不会读写真实库。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cineflow-files-api-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="cineflow-files-media-")
os.environ["CINEFLOW_DATA_DIR"] = TEMP_DATA
os.environ["CINEFLOW_DEMO"] = "false"
os.environ["CINEFLOW_ADMIN_PASSWORD"] = "cineflow"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402
from app.db import dumps, execute, now, query_one  # noqa: E402
from app.main import app  # noqa: E402


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

    media = Path(MEDIA_ROOT)
    inbox = media / "inbox"
    movie_dir = inbox / "Movie"
    show_dir = inbox / "Show" / "S01"
    move_dir = inbox / "MoveFolder"
    single_dir = inbox / "Single"
    conflict_dir = inbox / "Conflict"
    loop_dir = inbox / "Loop" / "sub"
    archive_dir = inbox / "archive"
    hidden_dir = inbox / ".hidden"
    trash_dir = inbox / "Filmix.Trash"
    for directory in (movie_dir, show_dir, move_dir, single_dir, conflict_dir, loop_dir, archive_dir, hidden_dir, trash_dir):
        directory.mkdir(parents=True, exist_ok=True)

    (inbox / "root.tc").write_text("root tc")
    (movie_dir / "Movie.2023.1080p.mkv").write_bytes(b"movie-video")
    (movie_dir / "Movie.2023.1080p.nfo").write_text("<movie/>")
    (movie_dir / "Movie.2023.1080p.chs.ass").write_text("subtitle")
    (movie_dir / "poster.jpg").write_bytes(b"jpg")
    (movie_dir / "trailer.tc").write_text("trailer tc")
    (movie_dir / "trailer.tc.ass").write_text("tc subtitle")
    (show_dir / "E01.mkv").write_bytes(b"show-video")
    (show_dir / "E01.ass").write_text("subtitle")
    (show_dir / "E01.tc").write_text("show tc")
    (move_dir / "A.mkv").write_bytes(b"move-video")
    (move_dir / "B.ass").write_text("move subtitle")
    (single_dir / "S.mkv").write_bytes(b"single-video")
    (single_dir / "S.ass").write_text("single subtitle")
    (conflict_dir / "one.mkv").write_bytes(b"conflict-source")
    (archive_dir / "one.mkv").write_bytes(b"conflict-target")
    (archive_dir / "old.tc").write_text("old")
    (hidden_dir / "secret.tc").write_text("secret")
    (trash_dir / "junk.tc").write_text("junk")
    (inbox / "MovieLink").symlink_to(movie_dir, target_is_directory=True)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cineflow"})
        expect("login", login.status_code, 200)

        source_id = execute(
            "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("文件管理测试源", "local", dumps({"base_path": str(media)}), now(), now()),
        )
        library_id = execute(
            "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
            "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "文件管理测试库",
                "inbox",
                "library",
                "auto",
                dumps(DEFAULT_NAMING),
                dumps(DEFAULT_SCRAPE),
                1,
                0,
                now(),
                now(),
            ),
        )

        def add_item(path: str, name: str) -> int:
            return execute(
                "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, status, "
                "match_key, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    library_id,
                    path,
                    name,
                    10,
                    0,
                    dumps({"title": name, "media_type": "movie", "raw": name}),
                    dumps({"tmdb_id": 999, "media_type": "movie", "title": name}),
                    "matched",
                    "movie:999",
                    now(),
                    now(),
                ),
            )

        movie_item = add_item("inbox/Movie/Movie.2023.1080p.mkv", "Movie.2023.1080p.mkv")
        show_item = add_item("inbox/Show/S01/E01.mkv", "E01.mkv")
        move_item = add_item("inbox/MoveFolder/A.mkv", "A.mkv")
        single_item = add_item("inbox/Single/S.mkv", "S.mkv")
        execute(
            "INSERT INTO attachment_links(library_id, video_path, attachment_path, created_at) VALUES(?,?,?,?)",
            (library_id, "inbox/Movie/Movie.2023.1080p.mkv", "inbox/Movie/Movie.2023.1080p.chs.ass", now()),
        )
        execute(
            "INSERT INTO attachment_links(library_id, video_path, attachment_path, created_at) VALUES(?,?,?,?)",
            (library_id, "inbox/Show/S01/E01.mkv", "inbox/Show/S01/E01.ass", now()),
        )
        execute(
            "INSERT INTO attachment_links(library_id, video_path, attachment_path, created_at) VALUES(?,?,?,?)",
            (library_id, "inbox/MoveFolder/A.mkv", "inbox/MoveFolder/B.ass", now()),
        )
        execute(
            "INSERT INTO attachment_links(library_id, video_path, attachment_path, created_at) VALUES(?,?,?,?)",
            (library_id, "inbox/Single/S.mkv", "inbox/Single/S.ass", now()),
        )

        # 根目录列表：所有类型都返回，包含文件夹。
        root = client.get(f"/api/libraries/{library_id}/files")
        expect("root list status", root.status_code, 200)
        root_data = root.json()
        expect("root path", root_data.get("path"), "")
        expect("root parent", root_data.get("parent"), None)
        expect("root dirs", root_data.get("counts", {}).get("dirs"), 10)
        expect("root files", root_data.get("counts", {}).get("files"), 1)
        root_entries = {entry["name"]: entry for entry in root_data.get("entries", [])}
        expect("root movie dir", root_entries.get("Movie", {}).get("is_dir"), True)
        expect("root tc kind", root_entries.get("root.tc", {}).get("kind"), "other")
        expect("root tc extension", root_entries.get("root.tc", {}).get("extension"), ".tc")
        expect("root tc modified", bool(root_entries.get("root.tc", {}).get("modified_at")), True)
        expect("root tc size", root_entries.get("root.tc", {}).get("size", 0) > 0, True)
        expect("root entry path relative", root_entries.get("Movie", {}).get("path"), "Movie")
        expect("root symlink dir", root_entries.get("MovieLink", {}).get("is_dir"), True)
        expect("root symlink flag", root_entries.get("MovieLink", {}).get("is_symlink"), True)

        # 子目录列表：视频 / NFO / 字幕 / 图片都展示。
        movie = client.get(f"/api/libraries/{library_id}/files", params={"path": "Movie"})
        expect("movie list status", movie.status_code, 200)
        movie_data = movie.json()
        expect("movie parent", movie_data.get("parent"), "")
        movie_entries = {entry["name"]: entry for entry in movie_data.get("entries", [])}
        expect("movie files", movie_data.get("counts", {}).get("files"), 6)
        expect("movie video kind", movie_entries.get("Movie.2023.1080p.mkv", {}).get("kind"), "video")
        expect("movie nfo kind", movie_entries.get("Movie.2023.1080p.nfo", {}).get("kind"), "meta")
        expect("movie subtitle kind", movie_entries.get("Movie.2023.1080p.chs.ass", {}).get("kind"), "subtitle")
        expect("movie image kind", movie_entries.get("poster.jpg", {}).get("kind"), "image")
        expect("movie size", movie_data.get("counts", {}).get("size", 0) > 0, True)

        nested = client.get(f"/api/libraries/{library_id}/files", params={"path": "Show/S01"})
        expect("nested status", nested.status_code, 200)
        expect("nested parent", nested.json().get("parent"), "Show")

        traversal = client.get(f"/api/libraries/{library_id}/files", params={"path": "../outside"})
        expect("traversal rejected", traversal.status_code, 400)

        # 普通包含匹配：当前目录。
        match_current = client.post(
            f"/api/libraries/{library_id}/files/match",
            json={"path": "", "pattern": ".tc", "mode": "contains", "recursive": False},
        )
        expect("match current status", match_current.status_code, 200)
        expect("match current total", match_current.json().get("total"), 1)
        expect("match current path", match_current.json()["matches"][0]["path"], "root.tc")

        # 包含匹配：递归，跳过隐藏目录和回收站。
        match_recursive = client.post(
            f"/api/libraries/{library_id}/files/match",
            json={"path": "", "pattern": ".tc", "mode": "contains", "recursive": True},
        )
        expect("match recursive status", match_recursive.status_code, 200)
        recursive_paths = {entry["path"] for entry in match_recursive.json().get("matches", [])}
        expect("recursive tc count", len(recursive_paths), 5)
        expect("recursive includes root", "root.tc" in recursive_paths, True)
        expect("recursive includes movie", "Movie/trailer.tc" in recursive_paths, True)
        expect("recursive includes tc-ass", "Movie/trailer.tc.ass" in recursive_paths, True)
        expect("recursive includes show", "Show/S01/E01.tc" in recursive_paths, True)
        expect("recursive includes archive", "archive/old.tc" in recursive_paths, True)
        expect("recursive skips hidden", ".hidden/secret.tc" in recursive_paths, False)
        expect("recursive skips trash", "Filmix.Trash/junk.tc" in recursive_paths, False)
        expect("recursive skips symlink dir", "MovieLink/trailer.tc" in recursive_paths, False)

        # 正则匹配：只匹配 .tc 结尾。
        match_regex = client.post(
            f"/api/libraries/{library_id}/files/match",
            json={"path": "", "pattern": r"\.tc$", "mode": "regex", "recursive": True},
        )
        expect("match regex status", match_regex.status_code, 200)
        regex_paths = {entry["path"] for entry in match_regex.json().get("matches", [])}
        expect("regex tc count", len(regex_paths), 4)
        expect("regex excludes tc-ass", "Movie/trailer.tc.ass" in regex_paths, False)

        invalid_regex = client.post(
            f"/api/libraries/{library_id}/files/match",
            json={"path": "", "pattern": "[", "mode": "regex", "recursive": False},
        )
        expect("invalid regex rejected", invalid_regex.status_code, 400)
        empty_pattern = client.post(
            f"/api/libraries/{library_id}/files/match",
            json={"path": "", "pattern": "", "mode": "contains"},
        )
        expect("empty pattern rejected", empty_pattern.status_code, 422)

        # 删除符号链接只删除链接本身，不删除链接指向的目录内容。
        delete_link = client.post(
            f"/api/libraries/{library_id}/files/delete",
            json={"paths": ["MovieLink"]},
        )
        expect("delete symlink status", delete_link.status_code, 200)
        expect("delete symlink link gone", (inbox / "MovieLink").exists(), False)
        expect("delete symlink target kept", (movie_dir / "trailer.tc").exists(), True)

        # 删除单个文件。
        delete_root = client.post(
            f"/api/libraries/{library_id}/files/delete",
            json={"paths": ["root.tc"]},
        )
        expect("delete root tc status", delete_root.status_code, 200)
        expect("delete root tc count", len(delete_root.json().get("deleted", [])), 1)
        expect("root tc gone", (inbox / "root.tc").exists(), False)

        # 不能删除影视库根目录。
        delete_library_root = client.post(
            f"/api/libraries/{library_id}/files/delete",
            json={"paths": [""]},
        )
        expect("delete library root rejected", delete_library_root.status_code, 400)

        # 删除视频：同步 items 与 attachment_links。
        delete_movie = client.post(
            f"/api/libraries/{library_id}/files/delete",
            json={"paths": ["Movie/Movie.2023.1080p.mkv"]},
        )
        expect("delete movie status", delete_movie.status_code, 200)
        expect("delete movie items", delete_movie.json().get("items_removed"), 1)
        expect("delete movie links", delete_movie.json().get("links_removed"), 1)
        expect("delete movie file gone", (movie_dir / "Movie.2023.1080p.mkv").exists(), False)
        expect("delete movie item gone", query_one("SELECT id FROM items WHERE id=?", (movie_item,)), None)
        expect(
            "delete movie link gone",
            query_one(
                "SELECT id FROM attachment_links WHERE attachment_path=?",
                ("inbox/Movie/Movie.2023.1080p.chs.ass",),
            ),
            None,
        )
        # 为后续“移动字幕”测试重新建立一条手动关联。
        execute(
            "INSERT INTO attachment_links(library_id, video_path, attachment_path, created_at) VALUES(?,?,?,?)",
            (library_id, "inbox/Movie/Movie.2023.1080p.mkv", "inbox/Movie/Movie.2023.1080p.chs.ass", now()),
        )

        # 删除文件夹：递归删除内部字幕 / NFO 等，并清理条目。
        delete_show = client.post(
            f"/api/libraries/{library_id}/files/delete",
            json={"paths": ["Show"]},
        )
        expect("delete show status", delete_show.status_code, 200)
        expect("delete show items", delete_show.json().get("items_removed"), 1)
        expect("delete show links", delete_show.json().get("links_removed"), 1)
        expect("delete show dir gone", (inbox / "Show").exists(), False)
        expect("delete show item gone", query_one("SELECT id FROM items WHERE id=?", (show_item,)), None)

        # 移动文件：同步 items 和手动关联的视频路径。
        move_file = client.post(
            f"/api/libraries/{library_id}/files/move",
            json={"paths": ["Movie/Movie.2023.1080p.nfo"], "destination": "archive"},
        )
        expect("move nfo status", move_file.status_code, 200)
        expect("move nfo count", len(move_file.json().get("moved", [])), 1)
        expect("move nfo exists", (archive_dir / "Movie.2023.1080p.nfo").exists(), True)
        expect("move nfo source gone", (movie_dir / "Movie.2023.1080p.nfo").exists(), False)

        # 移动视频：同步 item source_path / file_name 与关联 video_path。
        move_movie = client.post(
            f"/api/libraries/{library_id}/files/move",
            json={"paths": ["Movie/Movie.2023.1080p.chs.ass"], "destination": "archive"},
        )
        expect("move subtitle status", move_movie.status_code, 200)
        expect("move subtitle exists", (archive_dir / "Movie.2023.1080p.chs.ass").exists(), True)
        link = query_one(
            "SELECT video_path, attachment_path FROM attachment_links WHERE attachment_path=?",
            ("inbox/archive/Movie.2023.1080p.chs.ass",),
        )
        expect("move subtitle link updated", bool(link), True)
        expect("move subtitle video path kept", link["video_path"] if link else "", "inbox/Movie/Movie.2023.1080p.mkv")

        # 移动文件夹：内部条目和附件路径一起同步。
        move_folder = client.post(
            f"/api/libraries/{library_id}/files/move",
            json={"paths": ["MoveFolder"], "destination": "archive"},
        )
        expect("move folder status", move_folder.status_code, 200)
        expect("move folder exists", (archive_dir / "MoveFolder" / "A.mkv").exists(), True)
        expect("move folder source gone", (move_dir / "A.mkv").exists(), False)
        moved_item = query_one("SELECT source_path, file_name FROM items WHERE id=?", (move_item,))
        expect("move folder item path", moved_item["source_path"] if moved_item else "", "inbox/archive/MoveFolder/A.mkv")
        expect("move folder item name", moved_item["file_name"] if moved_item else "", "A.mkv")
        moved_link = query_one(
            "SELECT video_path, attachment_path FROM attachment_links WHERE attachment_path=?",
            ("inbox/archive/MoveFolder/B.ass",),
        )
        expect("move folder link video", moved_link["video_path"] if moved_link else "", "inbox/archive/MoveFolder/A.mkv")
        expect("move folder link attachment", moved_link["attachment_path"] if moved_link else "", "inbox/archive/MoveFolder/B.ass")

        # 移动单个视频：同步 item 的 source_path / file_name，并更新关联视频路径。
        move_single = client.post(
            f"/api/libraries/{library_id}/files/move",
            json={"paths": ["Single/S.mkv"], "destination": "archive"},
        )
        expect("move single status", move_single.status_code, 200)
        expect("move single exists", (archive_dir / "S.mkv").exists(), True)
        expect("move single source gone", (single_dir / "S.mkv").exists(), False)
        single_row = query_one("SELECT source_path, file_name FROM items WHERE id=?", (single_item,))
        expect("move single item path", single_row["source_path"] if single_row else "", "inbox/archive/S.mkv")
        expect("move single item name", single_row["file_name"] if single_row else "", "S.mkv")
        single_link = query_one(
            "SELECT video_path, attachment_path FROM attachment_links WHERE attachment_path=?",
            ("inbox/Single/S.ass",),
        )
        expect("move single link video", single_link["video_path"] if single_link else "", "inbox/archive/S.mkv")
        expect("move single link attachment kept", single_link["attachment_path"] if single_link else "", "inbox/Single/S.ass")

        # 目标同名冲突：整个移动拒绝，源文件保持不变。
        conflict = client.post(
            f"/api/libraries/{library_id}/files/move",
            json={"paths": ["Conflict/one.mkv"], "destination": "archive"},
        )
        expect("move conflict status", conflict.status_code, 400)
        expect("move conflict source kept", (conflict_dir / "one.mkv").exists(), True)
        expect("move conflict target kept", (archive_dir / "one.mkv").read_bytes(), b"conflict-target")

        # 不能移动到自身内部。
        loop = client.post(
            f"/api/libraries/{library_id}/files/move",
            json={"paths": ["Loop"], "destination": "Loop/sub"},
        )
        expect("move into self rejected", loop.status_code, 400)
        expect("loop folder kept", (loop_dir).exists(), True)

        # 活动任务期间拒绝删除 / 移动。
        task_id = execute(
            "INSERT INTO tasks(library_id, type, status, progress, total, done, stats, payload, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (library_id, "scan", "running", 0, 1, 0, "{}", "{}", now()),
        )
        delete_busy = client.post(
            f"/api/libraries/{library_id}/files/delete",
            json={"paths": ["archive/old.tc"]},
        )
        expect("delete while task running", delete_busy.status_code, 409)
        expect("busy file kept", (archive_dir / "old.tc").exists(), True)
        execute("UPDATE tasks SET status='success', finished_at=? WHERE id=?", (now(), task_id))

        # 重命名单个文件：不改父目录。
        rename_file = client.post(
            f"/api/libraries/{library_id}/files/rename",
            json={"path": "archive/old.tc", "name": "old-renamed.tc"},
        )
        expect("rename file status", rename_file.status_code, 200)
        expect("rename file exists", (archive_dir / "old-renamed.tc").exists(), True)
        expect("rename file source gone", (archive_dir / "old.tc").exists(), False)

        # 重命名文件夹：同步内部条目和手动关联路径。
        rename_folder = client.post(
            f"/api/libraries/{library_id}/files/rename",
            json={"path": "archive/MoveFolder", "name": "MoveFolderRenamed"},
        )
        expect("rename folder status", rename_folder.status_code, 200)
        expect("rename folder exists", (archive_dir / "MoveFolderRenamed" / "A.mkv").exists(), True)
        expect("rename folder source gone", (archive_dir / "MoveFolder").exists(), False)
        renamed_item = query_one("SELECT source_path, file_name FROM items WHERE id=?", (move_item,))
        expect(
            "rename folder item path",
            renamed_item["source_path"] if renamed_item else "",
            "inbox/archive/MoveFolderRenamed/A.mkv",
        )
        renamed_link = query_one(
            "SELECT video_path, attachment_path FROM attachment_links WHERE attachment_path=?",
            ("inbox/archive/MoveFolderRenamed/B.ass",),
        )
        expect(
            "rename folder link video",
            renamed_link["video_path"] if renamed_link else "",
            "inbox/archive/MoveFolderRenamed/A.mkv",
        )
        expect(
            "rename folder link attachment",
            renamed_link["attachment_path"] if renamed_link else "",
            "inbox/archive/MoveFolderRenamed/B.ass",
        )

        # 重命名冲突 / 路径分隔符：拒绝且源文件保持。
        rename_conflict = client.post(
            f"/api/libraries/{library_id}/files/rename",
            json={"path": "archive/old-renamed.tc", "name": "one.mkv"},
        )
        expect("rename conflict status", rename_conflict.status_code, 400)
        expect("rename conflict source kept", (archive_dir / "old-renamed.tc").exists(), True)
        rename_bad = client.post(
            f"/api/libraries/{library_id}/files/rename",
            json={"path": "archive/old-renamed.tc", "name": "../escape.tc"},
        )
        expect("rename path rejected", rename_bad.status_code, 400)

    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
