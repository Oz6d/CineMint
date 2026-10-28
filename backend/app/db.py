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
    target_name_override TEXT,
    error TEXT,
    manual INTEGER NOT NULL DEFAULT 0,
    match_key TEXT,
    season INTEGER,
    episode INTEGER,
    version INTEGER NOT NULL DEFAULT 0,
    version_label TEXT NOT NULL DEFAULT '',
    replaced_by INTEGER,
    subscription_id INTEGER,
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

-- 订阅源：夸克分享链接 / RSS 115 转存。
-- config 存各类型的抓取与过滤配置，schedule 存计划参数；
-- next_run_at 由调度器维护，便于重启后恢复。
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'quark',
    enabled INTEGER NOT NULL DEFAULT 1,
    library_id INTEGER REFERENCES libraries(id) ON DELETE CASCADE,
    config TEXT NOT NULL DEFAULT '{}',
    schedule TEXT NOT NULL DEFAULT '{}',
    next_run_at TEXT,
    deadline TEXT,
    auto_organize INTEGER NOT NULL DEFAULT 1,
    replace_versions INTEGER NOT NULL DEFAULT 1,
    notify_channel_ids TEXT NOT NULL DEFAULT '[]',
    last_run_at TEXT,
    last_status TEXT,
    last_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 远程文件台账：remote_key 是来源侧稳定指纹，避免重复转存。
CREATE TABLE IF NOT EXISTS subscription_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    remote_key TEXT NOT NULL,
    file_name TEXT NOT NULL DEFAULT '',
    remote_path TEXT NOT NULL DEFAULT '',
    saved_path TEXT,
    task_id TEXT NOT NULL DEFAULT '',
    season INTEGER,
    episode INTEGER,
    version INTEGER NOT NULL DEFAULT 0,
    version_label TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    item_id INTEGER REFERENCES items(id) ON DELETE SET NULL,
    target_path TEXT,
    error TEXT,
    raw TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(subscription_id, remote_key)
);

CREATE TABLE IF NOT EXISTS subscription_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    trigger TEXT NOT NULL DEFAULT 'schedule',
    progress REAL NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    stats TEXT NOT NULL DEFAULT '{}',
    message TEXT NOT NULL DEFAULT '',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS subscription_run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES subscription_runs(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);

-- 蜜柑 / Bangumi 的追更信息，可选关联到具体订阅。
CREATE TABLE IF NOT EXISTS bangumi_follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    bangumi_id INTEGER,
    title TEXT NOT NULL,
    title_original TEXT NOT NULL DEFAULT '',
    poster TEXT NOT NULL DEFAULT '',
    mikan_bangumi_id INTEGER,
    subgroup_id INTEGER,
    subgroup_name TEXT NOT NULL DEFAULT '',
    weekday INTEGER,
    air_time TEXT NOT NULL DEFAULT '',
    total_episodes INTEGER,
    latest_episode REAL,
    next_episode REAL,
    next_air_at TEXT,
    status TEXT NOT NULL DEFAULT 'following',
    feed_url TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    last_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(bangumi_id, subgroup_id)
);

CREATE TABLE IF NOT EXISTS notification_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    events TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_test_at TEXT,
    last_test_ok INTEGER,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER REFERENCES notification_channels(id) ON DELETE CASCADE,
    event TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);

-- 手动匹配学习结果：同一标题的后续文件可直接复用已确认的 TMDb 作品。
CREATE TABLE IF NOT EXISTS match_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias_key TEXT NOT NULL UNIQUE,
    alias_text TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL,
    tmdb_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    year INTEGER,
    match TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_match_aliases_tmdb ON match_aliases(media_type, tmdb_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_next ON subscriptions(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_subscription_entries_sub
    ON subscription_entries(subscription_id, status);
CREATE INDEX IF NOT EXISTS idx_subscription_entries_item ON subscription_entries(item_id);
CREATE INDEX IF NOT EXISTS idx_subscription_runs_sub ON subscription_runs(subscription_id, id);
CREATE INDEX IF NOT EXISTS idx_subscription_run_logs_run ON subscription_run_logs(run_id, id);
CREATE INDEX IF NOT EXISTS idx_bangumi_follows_weekday ON bangumi_follows(status, weekday);
CREATE INDEX IF NOT EXISTS idx_bangumi_follows_bangumi ON bangumi_follows(bangumi_id);
CREATE INDEX IF NOT EXISTS idx_notification_logs_channel
    ON notification_logs(channel_id, id);
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
    with db() as conn:
        conn.executescript(SCHEMA)
        # 早期库没有 match_key / manual；旧库也缺少订阅所需的季集与版本列。
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
        for column, ddl in {
            "match_key": "TEXT",
            "manual": "INTEGER NOT NULL DEFAULT 0",
            "season": "INTEGER",
            "episode": "INTEGER",
            "version": "INTEGER NOT NULL DEFAULT 0",
            "version_label": "TEXT NOT NULL DEFAULT ''",
            "replaced_by": "INTEGER",
            "subscription_id": "INTEGER",
            "target_name_override": "TEXT",
        }.items():
            if column not in cols:
                conn.execute(f"ALTER TABLE items ADD COLUMN {column} {ddl}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_match_key ON items(library_id, match_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_library_episode "
            "ON items(library_id, season, episode, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_subscription ON items(subscription_id)"
        )
        # 运行页需要进度；早期订阅表没有这三列。
        run_cols = {row["name"] for row in conn.execute("PRAGMA table_info(subscription_runs)")}
        for column, ddl in {
            "progress": "REAL NOT NULL DEFAULT 0",
            "total": "INTEGER NOT NULL DEFAULT 0",
            "done": "INTEGER NOT NULL DEFAULT 0",
        }.items():
            if column not in run_cols:
                conn.execute(f"ALTER TABLE subscription_runs ADD COLUMN {column} {ddl}")
        # 115 云下载用 task_id 在后续运行里继续轮询已提交的任务。
        entry_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(subscription_entries)")
        }
        if "task_id" not in entry_cols:
            conn.execute(
                "ALTER TABLE subscription_entries ADD COLUMN task_id TEXT NOT NULL DEFAULT ''"
            )
        # 追更记录需要记住蜜柑 RSS 地址与最近一次刷新错误。
        follow_cols = {
            row["name"] for row in conn.execute("PRAGMA table_info(bangumi_follows)")
        }
        for column, ddl in {
            "feed_url": "TEXT NOT NULL DEFAULT ''",
            "last_error": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in follow_cols:
                conn.execute(f"ALTER TABLE bangumi_follows ADD COLUMN {column} {ddl}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bangumi_follows_bangumi "
            "ON bangumi_follows(bangumi_id)"
        )
        # 老条目的季集只存在 parsed JSON 里，回填后版本替换才能命中历史条目。
        for row in conn.execute(
            "SELECT id, parsed, season, episode FROM items "
            "WHERE season IS NULL OR episode IS NULL"
        ).fetchall():
            try:
                parsed = json.loads(row["parsed"]) or {}
            except (TypeError, ValueError):
                continue
            season = parsed.get("season")
            episode = parsed.get("episode")
            if season is None and episode is None:
                continue
            conn.execute(
                "UPDATE items SET season=COALESCE(?, season), episode=COALESCE(?, episode) "
                "WHERE id=?",
                (season, episode, row["id"]),
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


def execute_count(sql: str, params: tuple[Any, ...] = ()) -> int:
    with db() as conn:
        return int(conn.execute(sql, params).rowcount)


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
