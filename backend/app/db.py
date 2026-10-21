from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    config TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS libraries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
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
    manual INTEGER NOT NULL DEFAULT 0,
    match_key TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(library_id, source_path)
);

CREATE INDEX IF NOT EXISTS idx_items_library_status ON items(library_id, status);

-- 手动把字幕 / 音轨 / 图片 / NFO 关联到某个视频。
-- 以路径为键，既能覆盖待整理文件，也能覆盖已整理到目标目录的文件。
CREATE TABLE IF NOT EXISTS attachment_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    video_path TEXT NOT NULL,
    attachment_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(library_id, attachment_path)
);

CREATE INDEX IF NOT EXISTS idx_attachment_links_library_video
    ON attachment_links(library_id, video_path);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id INTEGER REFERENCES libraries(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    stats TEXT NOT NULL DEFAULT '{}',
    payload TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS task_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_logs_task ON task_logs(task_id, id);

CREATE TABLE IF NOT EXISTS tmdb_cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- AI 交叉验证 / 文件名解析的 token 与费用记录。
-- 单价是调用时的快照，方便之后调价不影响历史统计。
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    uncached_tokens INTEGER NOT NULL DEFAULT 0,
    input_price REAL NOT NULL DEFAULT 0,
    cached_price REAL NOT NULL DEFAULT 0,
    output_price REAL NOT NULL DEFAULT 0,
    multiplier REAL NOT NULL DEFAULT 1,
    peak INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    reported_cost REAL NOT NULL DEFAULT 0,
    cost_source TEXT NOT NULL DEFAULT 'manual',
    estimated INTEGER NOT NULL DEFAULT 0,
    context TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ai_usage_created_at ON ai_usage(created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    # The MatchKey index references a generated column; create it separately so
    # existing databases can be upgraded without rebuilding the table.
    with db() as conn:
        conn.executescript(SCHEMA)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
        if "match_key" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN match_key TEXT")
        if "manual" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN manual INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_match_key ON items(library_id, match_key)"
        )
        # ai_usage 早期版本只有 prompt/output 单价；补缓存、峰谷字段并回填。
        ai_cols = {row["name"] for row in conn.execute("PRAGMA table_info(ai_usage)")}
        for column, ddl in {
            "cached_tokens": "INTEGER NOT NULL DEFAULT 0",
            "uncached_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cached_price": "REAL NOT NULL DEFAULT 0",
            "multiplier": "REAL NOT NULL DEFAULT 1",
            "peak": "INTEGER NOT NULL DEFAULT 0",
            "reported_cost": "REAL NOT NULL DEFAULT 0",
            "cost_source": "TEXT NOT NULL DEFAULT 'manual'",
        }.items():
            if column not in ai_cols:
                conn.execute(f"ALTER TABLE ai_usage ADD COLUMN {column} {ddl}")
        conn.execute(
            "UPDATE ai_usage SET uncached_tokens=prompt_tokens "
            "WHERE uncached_tokens=0 AND cached_tokens=0 AND prompt_tokens>0"
        )
        conn.execute(
            "UPDATE ai_usage SET cached_price=input_price "
            "WHERE cached_price=0 AND input_price>0"
        )
        # 剧集文件名模板旧默认值用「 - 」连接标题与季集，统一迁移为单个空格
        for row in conn.execute("SELECT id, naming FROM libraries").fetchall():
            try:
                naming = json.loads(row["naming"]) or {}
            except (TypeError, ValueError):
                continue
            episode_file = naming.get("episode_file")
            changed = False
            if isinstance(episode_file, str) and " - S{season" in episode_file:
                naming["episode_file"] = episode_file.replace(" - S{season", " S{season", 1)
                changed = True
            # 季文件夹默认不再补零：Season 1 / S1，而不是 Season 01 / S01
            season_folder = naming.get("season_folder")
            if season_folder in {"Season {season:02d}", "S{season:02d}"}:
                naming["season_folder"] = season_folder.replace("{season:02d}", "{season}")
                changed = True
            if changed:
                conn.execute(
                    "UPDATE libraries SET naming=? WHERE id=?",
                    (json.dumps(naming, ensure_ascii=False), row["id"]),
                )


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with db() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with db() as conn:
        cur = conn.execute(sql, params)
        return int(cur.lastrowid or 0)


def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value: Any) -> None:
    execute(
        """
        INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, json.dumps(value, ensure_ascii=False), now()),
    )


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
