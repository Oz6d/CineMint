"""待整理目录自动探测回归测试。

运行：cd backend && .venv/bin/python ../tests/test_auto_scan.py
脚本使用临时数据目录与假文件源，不会访问真实网盘或影视库。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["CINEFLOW_DATA_DIR"] = tempfile.mkdtemp(prefix="cineflow-auto-scan-")
os.environ["CINEFLOW_DEMO"] = "false"
os.environ["CINEFLOW_ADMIN_PASSWORD"] = "cineflow"

from app.core import auto_scan  # noqa: E402
from app.core.providers.base import Entry, SourceError, SourceNotFound  # noqa: E402
from app.db import dumps, execute, get_setting, init_db, loads, now, query_all, query_one, set_setting  # noqa: E402
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402

MB = 1024 * 1024


class FakeSource:
    """按路径返回固定目录内容的假文件源。"""

    def __init__(self, tree: dict[str, list[Entry]] | None = None) -> None:
        self.tree = tree or {}
        self.raise_at: dict[str, Exception] = {}
        self.visited: list[str] = []

    def list(self, path: str = "") -> list[Entry]:
        self.visited.append(path)
        if path in self.raise_at:
            raise self.raise_at[path]
        return list(self.tree.get(path, []))


def build_tree(extra_root: list[Entry] | None = None, deep: bool = False) -> dict[str, list[Entry]]:
    root = "inbox"
    show = f"{root}/show"
    season = f"{show}/Season 1"
    extras = f"{season}/Extras"
    deeper = f"{extras}/Deep"

    def file_entry(path: str, size: int = 20 * MB) -> Entry:
        return Entry(name=path.rsplit("/", 1)[-1], path=path, is_dir=False, size=size)

    def dir_entry(path: str) -> Entry:
        return Entry(name=path.rsplit("/", 1)[-1], path=path, is_dir=True, size=0)

    tree: dict[str, list[Entry]] = {
        root: [dir_entry(show), file_entry(f"{root}/Root.Movie.2020.mkv"), file_entry(f"{root}/small.mkv", MB), file_entry(f"{root}/note.txt", 1000), dir_entry(f"{root}/#recycle")],
        show: [dir_entry(season), file_entry(f"{show}/Show.S01E01.mkv", 30 * MB)],
        season: [file_entry(f"{season}/Show.S01E02.mkv", 30 * MB), dir_entry(extras)],
        extras: [file_entry(f"{extras}/Behind.mkv", 25 * MB)],
        f"{root}/#recycle": [file_entry(f"{root}/#recycle/old.mkv", 40 * MB)],
    }
    if deep:
        tree[extras].append(dir_entry(deeper))
        tree[deeper] = [file_entry(f"{deeper}/TooDeep.mkv", 40 * MB)]
    if extra_root:
        tree[root].extend(extra_root)
    return tree


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
            print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")

    source = FakeSource(build_tree(deep=True))
    auto_scan.build_source = lambda row: source  # type: ignore[assignment]
    refreshed: list[str] = []
    auto_scan.refresh_directory = lambda row, path: refreshed.append(path) or True  # type: ignore[assignment]

    source_id = execute(
        "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
        ("自动扫描测试源", "local", dumps({"base_path": "/tmp/none"}), now(), now()),
    )

    def make_library(name: str, *, demo: bool = False) -> int:
        return execute(
            "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, "
            "scrape, enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                name,
                "inbox",
                "library",
                "auto",
                dumps(DEFAULT_NAMING),
                dumps({**DEFAULT_SCRAPE, "enabled": False}),
                1,
                1 if demo else 0,
                now(),
                now(),
            ),
        )

    def library_row(library_id: int) -> dict:
        return query_one("SELECT * FROM libraries WHERE id=?", (library_id,))

    def tasks_for(library_id: int) -> list[dict]:
        return query_all("SELECT * FROM tasks WHERE library_id=? ORDER BY id", (library_id,))

    def state_for(library_id: int) -> dict | None:
        return query_one("SELECT * FROM library_auto_scan WHERE library_id=?", (library_id,))

    submitted: list[int] = []
    auto_scan.runner.submit = lambda task_id: submitted.append(task_id)  # type: ignore[assignment]

    # ---------- 指纹 ----------
    base = auto_scan.collect_signature(
        source, "inbox", min_size_mb=10, allow_iso=True, max_files=3000
    )
    same = auto_scan.collect_signature(
        FakeSource(build_tree()), "inbox", min_size_mb=10, allow_iso=True, max_files=3000
    )
    expect("同内容指纹稳定", base == same, True)

    changed_size = auto_scan.collect_signature(
        FakeSource(
            {
                **build_tree(),
                "inbox": [
                    Entry(name="Root.Movie.2020.mkv", path="inbox/Root.Movie.2020.mkv", is_dir=False, size=21 * MB),
                    Entry(name="show", path="inbox/show", is_dir=True),
                    Entry(name="#recycle", path="inbox/#recycle", is_dir=True),
                    Entry(name="note.txt", path="inbox/note.txt", is_dir=False, size=1000),
                    Entry(name="small.mkv", path="inbox/small.mkv", is_dir=False, size=MB),
                ],
            }
        ),
        "inbox",
        min_size_mb=10,
        allow_iso=True,
        max_files=3000,
    )
    expect("文件大小变化改变指纹", changed_size != base, True)

    added = auto_scan.collect_signature(
        FakeSource(build_tree([Entry(name="New.mkv", path="inbox/New.mkv", is_dir=False, size=50 * MB)])),
        "inbox",
        min_size_mb=10,
        allow_iso=True,
        max_files=3000,
    )
    expect("新增文件改变指纹", added != base, True)

    iso_ignored = auto_scan.collect_signature(
        FakeSource(build_tree([Entry(name="Disc.iso", path="inbox/Disc.iso", is_dir=False, size=60 * MB)])),
        "inbox",
        min_size_mb=10,
        allow_iso=False,
        max_files=3000,
    )
    expect("关闭 ISO 时忽略原盘", iso_ignored, base)

    iso_kept = auto_scan.collect_signature(
        FakeSource(build_tree([Entry(name="Disc.iso", path="inbox/Disc.iso", is_dir=False, size=60 * MB)])),
        "inbox",
        min_size_mb=10,
        allow_iso=True,
        max_files=3000,
    )
    expect("开启 ISO 时纳入指纹", iso_kept != base, True)

    deep_added = auto_scan.collect_signature(
        FakeSource(build_tree(deep=False)), "inbox", min_size_mb=10, allow_iso=True, max_files=3000
    )
    expect("深度 3 之外的新增不影响指纹", deep_added, base)

    recycle_changed = auto_scan.collect_signature(
        FakeSource(
            {
                **build_tree(),
                "inbox/#recycle": [
                    Entry(name="new.mkv", path="inbox/#recycle/new.mkv", is_dir=False, size=40 * MB)
                ],
            }
        ),
        "inbox",
        min_size_mb=10,
        allow_iso=True,
        max_files=3000,
    )
    expect("跳过回收站目录", recycle_changed, base)

    # ---------- 首次检查 / 无变化 / 变化 ----------
    lib1 = make_library("自动扫描库一")
    expect("首次检查触发扫描", auto_scan.check_library(library_row(lib1), force=True), "scanned")
    rows = tasks_for(lib1)
    expect("首次检查生成任务", len(rows), 1)
    expect("任务类型", rows[0]["type"], "scan")
    expect("自动触发标记", loads(rows[0]["payload"]).get("trigger"), "auto")
    expect("任务已提交执行器", submitted, [rows[0]["id"]])
    expect("刷新库根目录", refreshed, ["inbox"])
    state1 = state_for(lib1)
    expect("保存指纹", state1["fingerprint"], base)
    expect("首次检查无错误", state1["last_error"], "")
    execute("UPDATE tasks SET status='success' WHERE library_id=?", (lib1,))

    expect("无变化不扫描", auto_scan.check_library(library_row(lib1), force=True), "unchanged")
    expect("无变化不新增任务", len(tasks_for(lib1)), 1)

    source.tree = build_tree([Entry(name="New.mkv", path="inbox/New.mkv", is_dir=False, size=50 * MB)])
    expect("新增文件触发扫描", auto_scan.check_library(library_row(lib1), force=True), "scanned")
    expect("变化后新增任务", len(tasks_for(lib1)), 2)
    expect("状态记录最新任务", state_for(lib1)["last_task_id"], tasks_for(lib1)[1]["id"])
    expect("状态记录新指纹", state_for(lib1)["fingerprint"] != base, True)

    # ---------- 间隔 ----------
    expect("间隔内不重复检查", auto_scan.check_library(library_row(lib1)), "not_due")
    set_setting("auto_scan_interval_minutes", 1)
    expect("间隔下限 5 分钟", auto_scan.interval_minutes(), 5)
    set_setting("auto_scan_interval_minutes", 9999)
    expect("间隔上限 1440 分钟", auto_scan.interval_minutes(), 1440)
    set_setting("auto_scan_interval_minutes", 15)

    # ---------- 任务占用 ----------
    lib2 = make_library("自动扫描库二")
    busy_task = execute(
        "INSERT INTO tasks(library_id, type, status, payload, created_at) VALUES(?,?,?,?,?)",
        (lib2, "organize", "running", "{}", now()),
    )
    expect("有任务时顺延", auto_scan.check_library(library_row(lib2), force=True), "busy")
    expect("占用时不新增任务", len(tasks_for(lib2)), 1)
    expect("占用时不写指纹", state_for(lib2)["fingerprint"], None)
    execute("UPDATE tasks SET status='success' WHERE id=?", (busy_task,))

    # ---------- 演示库与开关 ----------
    lib3 = make_library("演示自动扫描库", demo=True)
    lib4 = make_library("自动扫描库四")
    execute("UPDATE libraries SET enabled=0 WHERE id NOT IN (?,?)", (lib3, lib4))
    set_setting("auto_scan_enabled", False)
    expect("开关关闭不检查", auto_scan.run_once(force=True), [])
    set_setting("auto_scan_enabled", True)
    results = auto_scan.run_once(force=True)
    expect("只检查非演示库", [row["library_id"] for row in results], [lib4])
    expect("非演示库生成任务", len(tasks_for(lib4)), 1)
    expect("演示库不扫描", len(tasks_for(lib3)), 0)

    # ---------- 探测失败 ----------
    lib5 = make_library("自动扫描库五")
    execute("UPDATE libraries SET enabled=1 WHERE id=?", (lib5,))
    failing = FakeSource({})
    failing.raise_at["inbox"] = SourceError("连接超时")
    auto_scan.build_source = lambda row: failing  # type: ignore[assignment]
    expect("探测失败不扫描", auto_scan.check_library(library_row(lib5), force=True), "error")
    expect("探测失败不建任务", len(tasks_for(lib5)), 0)
    expect("记录失败原因", "连接超时" in (state_for(lib5)["last_error"] or ""), True)

    # ---------- 根目录不存在 ----------
    lib6 = make_library("自动扫描库六")
    missing = FakeSource({})
    missing.raise_at["inbox"] = SourceNotFound("目录不存在")
    auto_scan.build_source = lambda row: missing  # type: ignore[assignment]
    expect("根目录丢失触发扫描", auto_scan.check_library(library_row(lib6), force=True), "scanned")
    expect("根目录丢失生成任务", len(tasks_for(lib6)), 1)
    expect("根目录丢失空指纹", state_for(lib6)["fingerprint"], auto_scan.EMPTY_SIGNATURE)
    execute("UPDATE tasks SET status='success' WHERE library_id=?", (lib6,))
    expect("空指纹不重复触发", auto_scan.check_library(library_row(lib6), force=True), "unchanged")

    # ---------- 设置读取 ----------
    from app.api import settings as settings_api

    set_setting("auto_scan_enabled", True)
    set_setting("auto_scan_interval_minutes", 30)
    payload = settings_api.read_settings()
    expect("设置返回自动扫描开关", payload["auto_scan_enabled"], True)
    expect("设置返回检查间隔", payload["auto_scan_interval_minutes"], 30)

    # ---------- 删除影视库清理状态 ----------
    from app.api import libraries as libraries_api

    lib7 = make_library("自动扫描库七")
    auto_scan.build_source = lambda row: FakeSource(build_tree())  # type: ignore[assignment]
    auto_scan.check_library(library_row(lib7), force=True)
    expect("删除前有探测状态", state_for(lib7) is not None, True)
    libraries_api.delete_library(lib7)
    expect("删除影视库清理状态", state_for(lib7), None)
    expect("删除影视库清理任务", tasks_for(lib7), [])

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
