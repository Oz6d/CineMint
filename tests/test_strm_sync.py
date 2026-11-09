"""strm 自动同步回归测试。

运行：cd backend && .venv/bin/python ../tests/test_strm_sync.py
使用假文件源与临时数据库，不访问真实影视库、OpenList 或 strm 目录。
"""

from __future__ import annotations

import os
import posixpath
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["CINEMINT_DATA_DIR"] = tempfile.mkdtemp(prefix="cinemint-strm-")
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from app.core import strm_sync  # noqa: E402
from app.core.providers.base import Entry, FileSource, SourceNotFound, norm  # noqa: E402
from app.db import execute, init_db, query_all, query_one  # noqa: E402


class FakeSource(FileSource):
    """最小文件源：维护目录集合与文件内容，路径均相对文件源根目录。"""

    type = "fake"

    def __init__(self, files: dict[str, str] | None = None, dirs: list[str] | None = None) -> None:
        super().__init__({})
        self.files = {norm(path): data for path, data in (files or {}).items()}
        self.dirs = {""}
        for directory in dirs or []:
            self.add_dir(directory)
        for path in self.files:
            self.add_dir(posixpath.dirname(path))

    def add_dir(self, path: str) -> None:
        path = norm(path)
        while path:
            self.dirs.add(path)
            path = posixpath.dirname(path)

    def list(self, path: str = "") -> list[Entry]:
        path = norm(path)
        if path not in self.dirs:
            raise SourceNotFound(f"目录不存在: {path}")
        entries: list[Entry] = []
        for directory in sorted(self.dirs):
            if directory and posixpath.dirname(directory) == path:
                entries.append(Entry(name=posixpath.basename(directory), path=directory, is_dir=True))
        for file_path, data in sorted(self.files.items()):
            if posixpath.dirname(file_path) == path:
                entries.append(
                    Entry(
                        name=posixpath.basename(file_path),
                        path=file_path,
                        is_dir=False,
                        size=len(data.encode("utf-8")),
                    )
                )
        return entries

    def exists(self, path: str) -> bool:
        path = norm(path)
        return path in self.files or path in self.dirs

    def stat(self, path: str) -> Entry | None:
        path = norm(path)
        if path in self.dirs:
            return Entry(name=posixpath.basename(path), path=path, is_dir=True)
        if path in self.files:
            return Entry(
                name=posixpath.basename(path),
                path=path,
                is_dir=False,
                size=len(self.files[path].encode("utf-8")),
            )
        return None

    def mkdir(self, path: str) -> None:
        self.add_dir(path)

    def read(self, path: str) -> bytes:
        return self.files[norm(path)].encode("utf-8")

    def write(self, path: str, data: bytes) -> None:
        path = norm(path)
        self.add_dir(posixpath.dirname(path))
        self.files[path] = data.decode("utf-8")

    def move(self, src: str, dst: str) -> None:
        src, dst = norm(src), norm(dst)
        if src not in self.files:
            raise strm_sync.SourceError(f"源文件不存在: {src}")
        if dst in self.files or dst in self.dirs:
            raise strm_sync.SourceError(f"目标已存在: {dst}")
        self.add_dir(posixpath.dirname(dst))
        self.files[dst] = self.files.pop(src)

    def delete(self, path: str) -> None:
        self.files.pop(norm(path), None)


def main() -> int:
    init_db()
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        if actual == expected:
            passed += 1
        else:
            print(f"FAIL {name}: expected {expected!r}, got {actual!r}")

    # 路径映射：只取相对 target_root 的目录，不扫描整个库。
    expect(
        "relative dir tv",
        strm_sync.relative_dir_for("115/For infuse/剧集/示例剧 (2024)/Season 1/示例剧 S01E01.mkv", "115/For infuse"),
        "剧集/示例剧 (2024)/Season 1",
    )
    expect(
        "relative dir movie",
        strm_sync.relative_dir_for("电影/示例电影 (2024)/示例电影 (2024).mkv", "电影"),
        "示例电影 (2024)",
    )
    expect(
        "relative dir root",
        strm_sync.relative_dir_for("电影/示例电影 (2024).mkv", ""),
        "电影",
    )
    expect(
        "relative dir same prefix guard",
        strm_sync.relative_dir_for("115/For infuse2/电影/a.mkv", "115/For infuse"),
        "115/For infuse2/电影",
    )
    expect("file stem", strm_sync.file_stem_for("a/b/电影 (2024).mkv"), "电影 (2024)")

    # 同名规则：完全同名、同名带后缀、作品级素材；其他集 / 无关文件不动。
    stem = "示例剧 S01E01"
    for name, matched in [
        ("示例剧 S01E01.strm", True),
        ("示例剧 S01E01.nfo", True),
        ("示例剧 S01E01-thumb.jpg", True),
        ("示例剧 S01E01.zh.ass", True),
        ("示例剧 S01E02.strm", False),
        ("示例剧 S01E011.strm", False),
        ("poster.jpg", True),
        ("tvshow.nfo", True),
        ("unrelated.txt", False),
    ]:
        expect(f"match {name}", strm_sync._matches(name, stem), matched)

    relative = "剧集/示例剧 (2024)/Season 1"
    source_root = "strm-source"
    target_root = "strm-target"
    source = FakeSource(
        {
            f"{source_root}/{relative}/示例剧 S01E01.strm": "strm",
            f"{source_root}/{relative}/示例剧 S01E01.nfo": "nfo",
            f"{source_root}/{relative}/示例剧 S01E02.strm": "other",
            f"{source_root}/{relative}/random.txt": "other",
        }
    )
    expect(
        "sync waiting missing dir",
        strm_sync.sync_one(source, source_root, target_root, "不存在/目录", stem)[0],
        "waiting",
    )
    expect(
        "sync waiting empty dir",
        strm_sync.sync_one(source, source_root, target_root, "空", stem)[0],
        "waiting",
    )

    status, error, copied = strm_sync.sync_one(source, source_root, target_root, relative, stem)
    expect("sync status", status, "done")
    expect("sync copied", copied, 2)
    expect("sync error", error, "")
    expect("target strm exists", f"{target_root}/{relative}/示例剧 S01E01.strm" in source.files, True)
    expect("target nfo exists", f"{target_root}/{relative}/示例剧 S01E01.nfo" in source.files, True)
    expect(
        "source strm kept",
        source.files[f"{source_root}/{relative}/示例剧 S01E01.strm"],
        "strm",
    )
    expect("source other episode kept", f"{source_root}/{relative}/示例剧 S01E02.strm" in source.files, True)
    expect("source unrelated kept", f"{source_root}/{relative}/random.txt" in source.files, True)

    # 目标已有同样内容：跳过复制；目标缺失时重新复制（复制式同步可自愈）。
    status, error, copied = strm_sync.sync_one(source, source_root, target_root, relative, stem)
    expect("repeat status", status, "done")
    expect("repeat copied", copied, 0)

    # 同名策略：加 (1) / 覆盖 / 跳过。
    source.files[f"{source_root}/{relative}/示例剧 S01E01.strm"] = "STRM"
    status, error, copied = strm_sync.sync_one(
        source, source_root, target_root, relative, stem, collision="suffix"
    )
    expect("suffix copied", copied, 1)
    expect(
        "suffix creates (1)",
        source.files.get(f"{target_root}/{relative}/示例剧 S01E01 (1).strm"),
        "STRM",
    )
    expect(
        "suffix keeps original target",
        source.files[f"{target_root}/{relative}/示例剧 S01E01.strm"],
        "strm",
    )

    source.files[f"{source_root}/{relative}/示例剧 S01E01.nfo"] = "NFO2"
    status, error, copied = strm_sync.sync_one(
        source, source_root, target_root, relative, stem, collision="skip"
    )
    expect("skip copied", copied, 0)
    expect(
        "skip keeps target nfo",
        source.files[f"{target_root}/{relative}/示例剧 S01E01.nfo"],
        "nfo",
    )

    status, error, copied = strm_sync.sync_one(
        source, source_root, target_root, relative, stem, collision="overwrite"
    )
    expect("non-force copies changed nfo only", copied, 1)
    status, error, copied = strm_sync.sync_one(
        source,
        source_root,
        target_root,
        relative,
        stem,
        collision="overwrite",
        force=True,
    )
    expect("force status", status, "done")
    expect("force copied", copied, 2)
    expect(
        "force overwrote target",
        source.files[f"{target_root}/{relative}/示例剧 S01E01.strm"],
        "STRM",
    )
    expect(
        "force overwrote nfo",
        source.files[f"{target_root}/{relative}/示例剧 S01E01.nfo"],
        "NFO2",
    )
    source.files[f"{source_root}/{relative}/示例剧 S01E01.strm"] = "strm"
    del source.files[f"{target_root}/{relative}/示例剧 S01E01.strm"]
    status, error, copied = strm_sync.sync_one(source, source_root, target_root, relative, stem)
    expect("recover copied", copied, 1)
    expect(
        "recover target exists",
        f"{target_root}/{relative}/示例剧 S01E01.strm" in source.files,
        True,
    )

    # 源文件更新（大小变化）时按覆盖策略更新目标，源文件仍保留。
    source.files[f"{source_root}/{relative}/示例剧 S01E01.strm"] = "new-content"
    status, error, copied = strm_sync.sync_one(
        source, source_root, target_root, relative, stem, collision="overwrite"
    )
    expect("updated status", status, "done")
    expect("updated copied", copied, 1)
    expect(
        "updated target",
        source.files[f"{target_root}/{relative}/示例剧 S01E01.strm"],
        "new-content",
    )
    expect("updated source kept", source.files[f"{source_root}/{relative}/示例剧 S01E01.strm"], "new-content")

    # 源缺失时删除目标旧文件（默认关闭时保留，只删同范围文件）。
    thumb = f"{target_root}/{relative}/示例剧 S01E01-thumb.jpg"
    other = f"{target_root}/{relative}/random-target.txt"
    source.files[thumb] = "thumb"
    source.files[other] = "keep"
    strm_sync.sync_one(
        source, source_root, target_root, relative, stem, collision="overwrite"
    )
    expect("missing kept by default", thumb in source.files, True)
    strm_sync.sync_one(
        source,
        source_root,
        target_root,
        relative,
        stem,
        collision="overwrite",
        delete_missing=True,
    )
    expect("missing deleted when enabled", thumb in source.files, False)
    expect("unrelated target kept", other in source.files, True)

    # 路径校验
    for label, src, dst in [
        ("empty", "", ""),
        ("same", source_root, source_root),
        ("nested", source_root, f"{source_root}/sub"),
        ("traversal", f"{source_root}/../x", target_root),
    ]:
        try:
            strm_sync.validate_roots(src, dst)
            raised = False
        except ValueError:
            raised = True
        expect(f"validate {label}", raised, True)
    expect(
        "validate ok",
        strm_sync.validate_roots(f"/{source_root}/", target_root),
        (source_root, target_root),
    )

    # 入队：未开启不入队；开启后重复整理会重置为 pending。
    execute(
        "INSERT INTO sources(name, type, config, created_at, updated_at) "
        "VALUES('test','fake','{}','now','now')"
    )
    source_id = query_one("SELECT id FROM sources ORDER BY id DESC LIMIT 1")["id"]
    execute(
        "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
        "strm_sync_enabled, strm_source_root, strm_target_root, created_at, updated_at) "
        "VALUES(?,'test','inbox','For infuse','auto','{\"collision\":\"overwrite\"}','{}',"
        "1,?,?, 'now','now')",
        (source_id, source_root, target_root),
    )
    library_id = query_one("SELECT id FROM libraries ORDER BY id DESC LIMIT 1")["id"]
    target_path = "For infuse/剧集/示例剧 (2024)/Season 1/示例剧 S01E01.mkv"
    expect("enqueue disabled", strm_sync.enqueue(library_id + 999, target_path), False)
    expect("enqueue enabled", strm_sync.enqueue(library_id, target_path), True)
    row = query_one("SELECT * FROM strm_sync_queue WHERE library_id=? AND target_path=?", (library_id, target_path))
    expect("queue relative dir", row["relative_dir"], "剧集/示例剧 (2024)/Season 1")
    expect("queue stem", row["file_stem"], "示例剧 S01E01")
    expect("queue force", row["force"], 1)
    execute("UPDATE strm_sync_queue SET status='done', attempts=9 WHERE id=?", (row["id"],))
    strm_sync.enqueue(library_id, target_path)
    row = query_one("SELECT * FROM strm_sync_queue WHERE id=?", (row["id"],))
    expect("re-enqueue status", row["status"], "pending")
    expect("re-enqueue attempts", row["attempts"], 0)

    # 后台处理：用假文件源替换 build_source。
    strm_sync.build_source = lambda _row: source
    emby_calls: list[int] = []
    strm_sync.emby.refresh_after_strm = lambda library: emby_calls.append(library) or False
    source.files[f"{source_root}/{relative}/示例剧 S01E01.strm"] = "strm"
    stats = strm_sync.process_pending()
    expect("process first pass waiting", stats["waiting"], 1)
    expect("strm completion triggers emby", library_id in emby_calls, True)
    expect(
        "force row rechecking",
        query_one("SELECT status FROM strm_sync_queue WHERE id=?", (row["id"],))["status"],
        "recheck",
    )
    expect(
        "force first pass overwrote",
        source.files[f"{target_root}/{relative}/示例剧 S01E01.strm"],
        "strm",
    )
    # 源更新后复查只复制变化；复查窗口结束标记 done。
    source.files[f"{source_root}/{relative}/示例剧 S01E01.strm"] = "strm-updated"
    stats = strm_sync.process_pending()
    expect(
        "recheck copies update",
        source.files[f"{target_root}/{relative}/示例剧 S01E01.strm"],
        "strm-updated",
    )
    execute(
        "UPDATE strm_sync_queue SET attempts=? WHERE id=?",
        (strm_sync.FORCE_RECHECK_ATTEMPTS, row["id"]),
    )
    stats = strm_sync.process_pending()
    expect("process done count", stats["done"], 1)
    expect("queue done", query_one("SELECT status FROM strm_sync_queue WHERE id=?", (row["id"],))["status"], "done")

    strm_sync.enqueue(library_id, "For infuse/剧集/示例剧 (2024)/Season 1/示例剧 S01E09.mkv")
    stats = strm_sync.process_pending()
    expect("process waiting count", stats["waiting"], 1)
    waiting = query_one(
        "SELECT attempts, status FROM strm_sync_queue WHERE library_id=? AND file_stem='示例剧 S01E09'",
        (library_id,),
    )
    expect("waiting attempts", waiting["attempts"], 1)
    expect("waiting status", waiting["status"], "pending")
    execute(
        "UPDATE strm_sync_queue SET attempts=? WHERE library_id=? AND file_stem='示例剧 S01E09'",
        (strm_sync.MAX_ATTEMPTS - 1, library_id),
    )
    stats = strm_sync.process_pending()
    expect("process timeout count", stats["failed"], 1)
    timed_out = query_one(
        "SELECT status, last_error FROM strm_sync_queue WHERE library_id=? AND file_stem='示例剧 S01E09'",
        (library_id,),
    )
    expect("timeout status", timed_out["status"], "failed")
    expect("timeout error", "未找到" in (timed_out["last_error"] or ""), True)

    strm_sync.enqueue(library_id, "For infuse/剧集/示例剧 (2024)/Season 1/示例剧 S01E08.mkv")
    execute("UPDATE libraries SET strm_sync_enabled=0 WHERE id=?", (library_id,))
    stats = strm_sync.process_pending()
    expect("disabled failed", stats["failed"] >= 1, True)
    disabled = query_one(
        "SELECT status, last_error FROM strm_sync_queue WHERE library_id=? AND file_stem='示例剧 S01E08'",
        (library_id,),
    )
    expect("disabled status", disabled["status"], "failed")
    expect("disabled error", "关闭" in (disabled["last_error"] or ""), True)

    # 剧集作品目录（Season 上一级）的 tvshow / poster / logo 等素材单独同步。
    show_relative = "剧集/示例剧 (2024)"
    for name in ("tvshow.nfo", "poster.jpg", "fanart.jpg", "logo.png", "folder.jpg"):
        source.files[f"{source_root}/{show_relative}/{name}"] = name
    episode_strm = f"{source_root}/{show_relative}/示例剧 S01E02.strm"
    source.files[episode_strm] = "episode"
    status, error, copied = strm_sync.sync_one(source, source_root, target_root, show_relative, "")
    expect("show dir status", status, "done")
    expect("show dir copied", copied, 5)
    expect("show dir error", error, "")
    for name in ("tvshow.nfo", "poster.jpg", "fanart.jpg", "logo.png", "folder.jpg"):
        expect(
            f"show asset {name}",
            f"{target_root}/{show_relative}/{name}" in source.files,
            True,
        )
        expect(
            f"show source {name} kept",
            f"{source_root}/{show_relative}/{name}" in source.files,
            True,
        )
    expect("show dir keeps episode strm", episode_strm in source.files, True)

    execute("UPDATE libraries SET strm_sync_enabled=1 WHERE id=?", (library_id,))
    expect(
        "enqueue show dir",
        strm_sync.enqueue(
            library_id,
            "For infuse/剧集/示例剧 (2024)/Season 1/示例剧 S01E05.mkv",
            show_dir="For infuse/剧集/示例剧 (2024)",
        ),
        True,
    )
    show_rows = query_all(
        "SELECT * FROM strm_sync_queue WHERE library_id=? AND file_stem=''", (library_id,)
    )
    expect("show queue rows", len(show_rows), 1)
    expect("show queue relative dir", show_rows[0]["relative_dir"], show_relative)
    execute("UPDATE strm_sync_queue SET status='done' WHERE id=?", (show_rows[0]["id"],))
    strm_sync.enqueue(
        library_id,
        "For infuse/剧集/示例剧 (2024)/Season 1/示例剧 S01E06.mkv",
        show_dir="For infuse/剧集/示例剧 (2024)",
    )
    expect(
        "show queue requeued after done",
        query_one("SELECT status FROM strm_sync_queue WHERE id=?", (show_rows[0]["id"],))["status"],
        "pending",
    )
    # 已经有待同步记录时不再重复插入（同一作品目录仍只有一行）。
    strm_sync.enqueue(
        library_id,
        "For infuse/剧集/示例剧 (2024)/Season 1/示例剧 S01E07.mkv",
        show_dir="For infuse/剧集/示例剧 (2024)",
    )
    expect(
        "show queue deduped",
        len(query_all("SELECT id FROM strm_sync_queue WHERE library_id=? AND file_stem=''", (library_id,))),
        1,
    )
    # 没有季目录时作品目录就是视频目录，不需要额外记录。
    before = len(query_all("SELECT id FROM strm_sync_queue WHERE library_id=?", (library_id,)))
    strm_sync.enqueue(
        library_id,
        "For infuse/电影/示例电影 (2024)/示例电影 (2024).mkv",
        show_dir="For infuse/电影/示例电影 (2024)",
    )
    after = len(query_all("SELECT id FROM strm_sync_queue WHERE library_id=?", (library_id,)))
    expect("same dir no show row", after - before, 1)

    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
