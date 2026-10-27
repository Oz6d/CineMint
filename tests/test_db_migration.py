"""订阅相关数据模型的旧库迁移回归测试。

运行：cd backend && .venv/bin/python ../tests/test_db_migration.py
使用临时数据目录，不触碰真实库。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cineflow-db-migration-")
os.environ["CINEFLOW_DATA_DIR"] = TEMP_DATA
os.environ["CINEFLOW_DEMO"] = "false"

from app.db import init_db, query_all, query_one  # noqa: E402


OLD_SCHEMA = """
CREATE TABLE sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    config TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE libraries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL DEFAULT '',
    target_root TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT 'auto',
    naming TEXT NOT NULL,
    scrape TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_demo INTEGER NOT NULL DEFAULT 0,
    last_scan_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    parsed TEXT NOT NULL,
    match TEXT,
    candidates TEXT,
    status TEXT NOT NULL DEFAULT 'review',
    target_path TEXT,
    error TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(library_id, source_path)
);
CREATE TABLE subscription_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    trigger TEXT NOT NULL DEFAULT 'schedule',
    stats TEXT NOT NULL DEFAULT '{}',
    message TEXT NOT NULL DEFAULT '',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE TABLE subscription_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL,
    remote_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    item_id INTEGER,
    UNIQUE(subscription_id, remote_key)
);
"""


def main() -> int:
    db_path = Path(TEMP_DATA) / "cineflow.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO sources(name,type,config,created_at,updated_at) VALUES('old','local','{}','x','x')"
    )
    conn.execute(
        "INSERT INTO libraries(source_id,name,naming,scrape,created_at,updated_at) "
        "VALUES(1,'old','{}','{}','x','x')"
    )
    parsed = json.dumps({"season": 2, "episode": 5, "title": "Old Show"})
    conn.execute(
        "INSERT INTO items(library_id,source_path,file_name,parsed,status,created_at,updated_at) "
        "VALUES(1,'Old Show S02E05.mkv','Old Show S02E05.mkv',?,'organized','x','x')",
        (parsed,),
    )
    conn.commit()
    conn.close()

    init_db()
    init_db()

    failures: list[str] = []
    item_cols = {row["name"] for row in query_all("PRAGMA table_info(items)")}
    for column in {
        "season",
        "episode",
        "version",
        "version_label",
        "replaced_by",
        "subscription_id",
    }:
        if column not in item_cols:
            failures.append(f"items.{column} missing")

    run_cols = {row["name"] for row in query_all("PRAGMA table_info(subscription_runs)")}
    for column in {"progress", "total", "done"}:
        if column not in run_cols:
            failures.append(f"subscription_runs.{column} missing")

    entry_cols = {
        row["name"] for row in query_all("PRAGMA table_info(subscription_entries)")
    }
    if "task_id" not in entry_cols:
        failures.append("subscription_entries.task_id missing")

    tables = {row["name"] for row in query_all("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in {
        "subscriptions",
        "subscription_entries",
        "subscription_runs",
        "bangumi_follows",
        "notification_channels",
        "notification_logs",
        "match_aliases",
    }:
        if table not in tables:
            failures.append(f"table {table} missing")

    item = query_one("SELECT * FROM items WHERE file_name='Old Show S02E05.mkv'")
    if not item:
        failures.append("old item lost")
    else:
        if int(item.get("season") or 0) != 2 or int(item.get("episode") or 0) != 5:
            failures.append(f"season/episode backfill wrong: {item.get('season')}/{item.get('episode')}")
        if int(item.get("version") or 0) != 0:
            failures.append("version default wrong")
        if item.get("status") != "organized":
            failures.append("old status changed")

    indexes = {row["name"] for row in query_all("SELECT name FROM sqlite_master WHERE type='index'")}
    for index in {"idx_items_library_episode", "idx_subscriptions_next", "idx_subscription_entries_sub"}:
        if index not in indexes:
            failures.append(f"index {index} missing")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("db migration cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
