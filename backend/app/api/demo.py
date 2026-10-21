from __future__ import annotations

import shutil
from typing import Any
from urllib.parse import quote

from ..config import settings
from ..db import dumps, execute, now, query_all, query_one, set_setting
from ..core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE, build_target
from ..core.parser import parse_media_name
from ..core.providers import build_source


def _art(slug: str, title: str, year: Any = "", kind: str = "poster") -> str:
    return f"/api/demo/art/{kind}/{slug}?t={quote(str(title))}&y={year}"


DEMO_WORKS: list[dict[str, Any]] = [
    {
        "slug": "rangzidanfei",
        "title": "让子弹飞",
        "original_title": "让子弹飞",
        "year": 2010,
        "tmdb_id": 43182,
        "media_type": "movie",
        "vote_average": 8.8,
        "genres": ["剧情", "喜剧", "动作"],
        "overview": "民国年间，马邦德买官赴任，途中遭遇张麻子一伙劫匪，一场斗智斗勇的较量就此展开。",
        "items": [
            {
                "file_name": "[电影天堂www.dy2018.com]让子弹飞2010BD1280超清.mkv",
                "status": "organized",
                "target": "电影/让子弹飞 (2010)/让子弹飞 (2010).mkv",
            }
        ],
    },
    {
        "slug": "qianyuqianxun",
        "title": "千与千寻",
        "original_title": "千と千尋の神隠し",
        "year": 2001,
        "tmdb_id": 129,
        "media_type": "movie",
        "vote_average": 8.5,
        "genres": ["动画", "奇幻", "家庭"],
        "overview": "少女千寻误入神灵世界，父母被变成猪，她必须在汤屋工作并找回自己的名字。",
        "items": [
            {
                "file_name": "千与千寻.Spirited.Away.2001.BluRay.1080p.mkv",
                "status": "review",
                "confidence": 0.62,
            }
        ],
    },
    {
        "slug": "aobenhaimo",
        "title": "奥本海默",
        "original_title": "Oppenheimer",
        "year": 2023,
        "tmdb_id": 872585,
        "media_type": "movie",
        "vote_average": 8.1,
        "genres": ["剧情", "历史"],
        "overview": "原子弹之父罗伯特·奥本海默的传记故事。",
        "items": [
            {
                "file_name": "Oppenheimer.2023.2160p.WEB-DL.HDR.DDP5.1.Atmos.mkv",
                "status": "matched",
            }
        ],
    },
    {
        "slug": "shaju2",
        "title": "沙丘2",
        "original_title": "Dune: Part Two",
        "year": 2024,
        "tmdb_id": 693134,
        "media_type": "movie",
        "vote_average": 8.2,
        "genres": ["科幻", "冒险"],
        "overview": "保罗·厄崔迪与弗雷曼人联手，向哈克南家族发起复仇。",
        "items": [
            {"file_name": "4K.HDR.杜比视界.沙丘2.2024.mkv", "status": "unmatched"},
        ],
    },
    {
        "slug": "laoyouji",
        "title": "老友记",
        "original_title": "Friends",
        "year": 1994,
        "tmdb_id": 1668,
        "media_type": "tv",
        "vote_average": 8.9,
        "genres": ["喜剧", "剧情"],
        "overview": "六位好友在曼哈顿的生活、爱情与欢笑。",
        "seasons": [1],
        "items": [
            {
                "file_name": "老友记.1994.S01E01.mkv",
                "season": 1, "episode": 1, "status": "organized",
                "target": "剧集/老友记 (1994)/Season 1/老友记 - S01E01.mkv",
            },
            {
                "file_name": "老友记(1994) E01-E04.mkv",
                "season": 1, "episode": 2, "status": "matched",
            },
            {
                "file_name": "Friends.S01E03.1080p.WEB-DL.mkv",
                "season": 1, "episode": 3, "status": "matched",
            },
            {
                "file_name": "老友记.S01E04.zh.srt.mkv",
                "season": 1, "episode": 4, "status": "review",
                "confidence": 0.55,
            },
        ],
    },
    {
        "slug": "qingyinnian",
        "title": "庆余年",
        "original_title": "庆余年",
        "year": 2019,
        "tmdb_id": 92756,
        "media_type": "tv",
        "vote_average": 8.0,
        "genres": ["剧情", "古装"],
        "overview": "少年范闲带着现代记忆降生于庆国，在庙堂与江湖间步步为营。",
        "seasons": [1],
        "items": [
            {"file_name": "庆余年.Joy of Life (2019)/S01/庆余年.2019.S01E01.4K.mkv", "season": 1, "episode": 1, "status": "matched"},
            {"file_name": "庆余年.Joy of Life (2019)/S01/E02.mkv", "season": 1, "episode": 2, "status": "matched"},
            {"file_name": "庆余年.Joy of Life (2019)/S01/E03.2160p.mkv", "season": 1, "episode": 3, "status": "matched"},
        ],
    },
    {
        "slug": "guaqiwuyu",
        "title": "怪奇物语",
        "original_title": "Stranger Things",
        "year": 2016,
        "tmdb_id": 66732,
        "media_type": "tv",
        "vote_average": 8.6,
        "genres": ["剧情", "奇幻", "悬疑"],
        "overview": "小镇男孩神秘失踪，朋友们遇见拥有超能力的女孩。",
        "seasons": [1],
        "items": [
            {"file_name": "怪奇物语.2016.S01E01.1080p.mkv", "season": 1, "episode": 1, "status": "review", "confidence": 0.6},
            {"file_name": "Stranger.Things.S01E02.HDTV.mkv", "season": 1, "episode": 2, "status": "review", "confidence": 0.58},
        ],
    },
    {
        "slug": "santi",
        "title": "三体",
        "original_title": "三体",
        "year": 2023,
        "tmdb_id": 124364,
        "media_type": "tv",
        "vote_average": 8.7,
        "genres": ["科幻", "剧情"],
        "overview": "纳米科学家汪淼被卷入一场跨越时空的科学阴谋。",
        "seasons": [1],
        "items": [
            {
                "file_name": "三体 (2023)/Season 1/E01.mkv",
                "season": 1, "episode": 1, "status": "organized",
                "target": "剧集/三体 (2023)/Season 1/三体 - S01E01.mkv",
            },
            {
                "file_name": "三体 (2023)/Season 1/E02.mkv",
                "season": 1, "episode": 2, "status": "organized",
                "target": "剧集/三体 (2023)/Season 1/三体 - S01E02.mkv",
            },
        ],
    },
]


def _write_sparse(path, size_mb: int = 24) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    size = size_mb * 1024 * 1024
    with open(path, "wb") as handle:
        handle.seek(size - 1)
        handle.write(b"\x00")


def _demo_match(work: dict[str, Any]) -> dict[str, Any]:
    return {
        "tmdb_id": work["tmdb_id"],
        "media_type": work["media_type"],
        "title": work["title"],
        "original_title": work["original_title"],
        "year": work["year"],
        "overview": work["overview"],
        "vote_average": work["vote_average"],
        "vote_count": 1000,
        "genres": work["genres"],
        "poster_path": _art(work["slug"], work["title"], work["year"], "poster"),
        "backdrop_path": _art(work["slug"], work["title"], work["year"], "backdrop"),
        "logo_path": "",
        "popularity": 50.0,
        "cast": [],
        "runtime": 120,
        "status": "Released",
        "confidence": 1.0,
    }


def seed_demo() -> dict[str, Any]:
    existing = query_one("SELECT id FROM libraries WHERE is_demo=1 LIMIT 1")
    if existing:
        return {"created": False, "library_id": existing["id"]}

    base = settings.demo_dir
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    (base / "inbox").mkdir(parents=True, exist_ok=True)
    (base / "library").mkdir(parents=True, exist_ok=True)

    source_id = execute(
        "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
        ("演示·本地存储", "local", dumps({"base_path": str(base)}), now(), now()),
    )
    library_id = execute(
        "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
        "is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            "演示影视库",
            "inbox",
            "library",
            "auto",
            dumps(DEFAULT_NAMING),
            dumps(DEFAULT_SCRAPE),
            1,
            now(),
            now(),
        ),
    )

    total_items = 0
    for work in DEMO_WORKS:
        match = _demo_match(work)
        for item in work["items"]:
            file_name = item["file_name"]
            parsed = parse_media_name(file_name.rsplit("/", 1)[-1]).to_dict()
            parsed.update(
                {
                    "title": work["title"],
                    "title_alt": work["original_title"],
                    "year": work["year"],
                    "media_type": work["media_type"],
                    "season": item.get("season"),
                    "episode": item.get("episode"),
                }
            )
            item_match = {**match, "confidence": item.get("confidence", 1.0)}
            status = item["status"]
            source_path = f"inbox/{file_name}"
            if status == "organized":
                target = f"library/{item['target']}"
                _write_sparse(base / target, 18)
                execute(
                    "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, "
                    "candidates, status, target_path, match_key, manual, is_demo, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        library_id, source_path, file_name.rsplit("/", 1)[-1],
                        18 * 1024 * 1024, 0, dumps(parsed), dumps(item_match), dumps([item_match]),
                        "organized", item["target"], f"{work['media_type']}:{work['tmdb_id']}", 1, 1, now(), now(),
                    ),
                )
            else:
                _write_sparse(base / source_path, 24)
                execute(
                    "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, "
                    "candidates, status, match_key, is_demo, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        library_id, source_path, file_name.rsplit("/", 1)[-1],
                        24 * 1024 * 1024, 0, dumps(parsed), dumps(item_match), dumps([item_match]),
                        status, f"{work['media_type']}:{work['tmdb_id']}", 1, now(), now(),
                    ),
                )
            total_items += 1

    # 两条历史任务，让任务中心不是空的
    scan_id = execute(
        "INSERT INTO tasks(library_id, type, status, progress, total, done, stats, payload, "
        "created_at, started_at, finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            library_id, "scan", "success", 100, total_items, total_items,
            dumps({"found": total_items, "matched": 10, "review": 4, "unmatched": 2, "ignored": 0}),
            "{}", now(), now(), now(),
        ),
    )
    for message, level in [
        ("开始扫描「演示影视库」", "info"),
        (f"扫描到 {total_items} 个视频文件（共检查 {total_items + 7} 个条目）", "info"),
        ("扫描完成：匹配 10，待确认 4，未识别 2，忽略 0", "info"),
    ]:
        execute(
            "INSERT INTO task_logs(task_id, ts, level, message) VALUES(?,?,?,?)",
            (scan_id, now(), level, message),
        )

    organize_id = execute(
        "INSERT INTO tasks(library_id, type, status, progress, total, done, stats, payload, "
        "created_at, started_at, finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            library_id, "organize", "success", 100, 6, 6,
            dumps({"success": 6, "failed": 0, "skipped": 0}),
            dumps({"item_ids": []}), now(), now(), now(),
        ),
    )
    for message in [
        "开始整理 6 个文件",
        "完成 [1/6]: 让子弹飞2010BD1280超清.mkv → 电影/让子弹飞 (2010)/让子弹飞 (2010).mkv",
        "完成 [6/6]: 三体 (2023)/Season 1/E02.mkv → 剧集/三体 (2023)/Season 1/三体 - S01E02.mkv",
        "整理完成：成功 6，失败 0，跳过 0",
    ]:
        execute(
            "INSERT INTO task_logs(task_id, ts, level, message) VALUES(?,?,?,?)",
            (organize_id, now(), "info", message),
        )

    execute("UPDATE libraries SET last_scan_at=? WHERE id=?", (now(), library_id))
    set_setting("demo_seeded", True)
    return {"created": True, "library_id": library_id, "items": total_items}


def clear_demo() -> dict[str, Any]:
    libraries = query_all("SELECT id, source_id FROM libraries WHERE is_demo=1")
    for library in libraries:
        for task in query_all("SELECT id FROM tasks WHERE library_id=?", (library["id"],)):
            execute("DELETE FROM task_logs WHERE task_id=?", (task["id"],))
        execute("DELETE FROM tasks WHERE library_id=?", (library["id"],))
        execute("DELETE FROM items WHERE library_id=?", (library["id"],))
        execute("DELETE FROM libraries WHERE id=?", (library["id"],))
        execute("DELETE FROM sources WHERE id=?", (library["source_id"],))
    if settings.demo_dir.exists():
        shutil.rmtree(settings.demo_dir, ignore_errors=True)
    set_setting("demo_seeded", False)
    return {"ok": True, "removed": len(libraries)}
