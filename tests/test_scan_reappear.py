"""已整理文件的源路径重新出现时，扫描应恢复为待处理的回归测试。

覆盖：
- 同名文件重新下载 / 重新转存回待整理目录后，不再被「已整理」静默跳过；
- 手动匹配过的条目恢复后仍保持已匹配状态；
- 没有匹配的条目恢复为未识别，等待重新识别；
- 源文件不存在时不改动已整理记录。

运行：cd backend && .venv/bin/python ../tests/test_scan_reappear.py
使用临时数据目录与临时本地媒体，不读写真实影视库。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cinemint-scan-reappear-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="cinemint-scan-reappear-media-")
os.environ["CINEMINT_DATA_DIR"] = TEMP_DATA
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from app.core import pipeline  # noqa: E402
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402
from app.db import dumps, execute, init_db, now, query_one, set_setting  # noqa: E402


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    init_db()
    set_setting("min_size_mb", 0)
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    media = Path(MEDIA_ROOT)
    show_dir = media / "inbox" / "Mystery"
    show_dir.mkdir(parents=True)
    media_file = show_dir / "Mystery.S01E01.mkv"
    media_file.write_bytes(b"video")

    source_id = execute(
        "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
        ("重新出现测试源", "local", dumps({"base_path": str(media)}), now(), now()),
    )
    library_id = execute(
        "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
        "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            "重新出现测试库",
            "inbox",
            "library",
            "auto",
            dumps(DEFAULT_NAMING),
            dumps({**DEFAULT_SCRAPE, "enabled": False}),
            1,
            0,
            now(),
            now(),
        ),
    )

    source_path = "inbox/Mystery/Mystery.S01E01.mkv"
    match = {
        "tmdb_id": 900001,
        "media_type": "tv",
        "title": "Mystery",
        "original_title": "Mystery",
        "year": 2024,
    }
    parsed = {
        "raw": "Mystery.S01E01.mkv",
        "title": "Mystery",
        "media_type": "tv",
        "season": 1,
        "episode": 1,
    }

    def insert_item(manual: int, with_match: bool) -> int:
        execute("DELETE FROM items WHERE library_id=?", (library_id,))
        return execute(
            "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, "
            "status, target_path, manual, match_key, season, episode, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                library_id,
                source_path,
                "Mystery.S01E01.mkv",
                5,
                0,
                dumps(parsed),
                dumps(match) if with_match else None,
                "organized",
                "library/Mystery/Mystery S01E01.mkv",
                manual,
                "tv:900001" if with_match else None,
                1,
                1,
                now(),
                now(),
            ),
        )

    # 手动匹配过的条目重新出现：恢复为 matched，清掉旧目标路径。
    item_id = insert_item(manual=1, with_match=True)
    pipeline.scan_library(library_id)
    row = query_one("SELECT * FROM items WHERE id=?", (item_id,))
    expect("manual reappear status", row["status"], "matched")
    expect("manual reappear target cleared", row["target_path"], None)
    expect("manual flag kept", row["manual"], 1)

    # 没有匹配的条目重新出现：恢复为 unmatched，等待重新识别。
    item_id = insert_item(manual=0, with_match=False)
    pipeline.scan_library(library_id)
    row = query_one("SELECT * FROM items WHERE id=?", (item_id,))
    expect("unmatched reappear status", row["status"], "unmatched")
    expect("unmatched reappear target cleared", row["target_path"], None)

    # 已整理但源文件不在扫描结果里：保持 organized，不被改动。
    item_id = insert_item(manual=1, with_match=True)
    execute(
        "UPDATE items SET source_path=? WHERE id=?",
        ("inbox/Other/NotHere.mkv", item_id),
    )
    pipeline.scan_library(library_id)
    row = query_one("SELECT * FROM items WHERE id=?", (item_id,))
    expect("absent file keeps organized", row["status"], "organized")
    expect("absent file keeps target", row["target_path"], "library/Mystery/Mystery S01E01.mkv")

    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
