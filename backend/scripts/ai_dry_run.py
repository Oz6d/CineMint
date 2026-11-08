"""只读 dry-run：对最近未识别 / 待确认条目跑一遍识别链路，不修改条目、不整理。

用法：
  CINEMINT_DATA_DIR=/data PYTHONPATH=/path/to/newapp \\
    python scripts/ai_dry_run.py --limit 30

输出每个条目的原状态、模拟识别结果、AI 来源（联网 / 普通 / 两轮）、错误；
最后打印汇总。只写 ai_usage 统计与目录结构缓存，不写 items / 文件。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from app.core.matcher import match_item
from app.core.metadata.tmdb import client_from_settings
from app.core.parser import parse_media_name
from app.db import get_setting, loads, query_all


def folder_context(source_path: str):
    from app.core.pipeline import _folder_context

    return _folder_context(source_path)


def merge_context(parsed, folder):
    from app.core.pipeline import _merge_context

    return _merge_context(parsed, folder)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--library", type=int, default=0)
    parser.add_argument(
        "--exclude-tmdb-id",
        action="store_true",
        help="跳过文件名里已经带 {tmdbid-...} 的条目",
    )
    parser.add_argument(
        "--statuses",
        default="unmatched,review,failed",
        help="要回放的条目状态，逗号分隔；已匹配回放用 matched",
    )
    args = parser.parse_args()

    statuses = [part.strip() for part in args.statuses.split(",") if part.strip()]
    placeholders = ",".join("?" for _ in statuses)
    sql = (
        "SELECT id, library_id, source_path, file_name, status, match_key FROM items "
        f"WHERE status IN ({placeholders})"
    )
    params: tuple = tuple(statuses)
    if args.library:
        sql += " AND library_id=?"
        params = (*params, args.library)
    if args.exclude_tmdb_id:
        sql += " AND file_name NOT LIKE '%{tmdbid-%'"
    sql += " ORDER BY id DESC LIMIT ?"
    rows = query_all(sql, (*params, max(1, min(args.limit, 200))))
    client = client_from_settings()
    if not client.configured:
        print("TMDb 未配置，无法评估")
        return 1
    auto_threshold = float(get_setting("auto_threshold", 0.72) or 0.72)
    review_threshold = float(get_setting("review_threshold", 0.5) or 0.5)

    seen_dirs: set[str] = set()
    stats = Counter()
    results = []
    for row in rows:
        source_path = row["source_path"]
        directory = source_path.rsplit("/", 1)[0] if "/" in source_path else ""
        if directory in seen_dirs:
            stats["skipped_same_dir"] += 1
            continue
        seen_dirs.add(directory)
        parsed = parse_media_name(row["file_name"])
        folder = folder_context(source_path)
        parsed = merge_context(parsed, folder)
        try:
            result = match_item(
                parsed,
                folder,
                client,
                auto_threshold=auto_threshold,
                review_threshold=review_threshold,
                folder_path=directory,
            )
        except Exception as exc:
            result = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        best = result.get("best") or {}
        ai = result.get("ai") or {}
        predicted = (
            f"{best.get('media_type')}:{best.get('tmdb_id')}" if best.get("tmdb_id") else ""
        )
        stored = row["match_key"] or ""
        record = {
            "item_id": row["id"],
            "file": row["file_name"],
            "before": row["status"],
            "after": result.get("status"),
            "source": result.get("source"),
            "ai_source": ai.get("source"),
            "title": best.get("title"),
            "original_title": best.get("original_title"),
            "year": best.get("year"),
            "confidence": result.get("confidence"),
            "predicted": predicted,
            "stored": stored,
            "hit": (predicted == stored) if stored else None,
            "ai_error": result.get("ai_error") or result.get("error"),
        }
        if stored:
            stats["hit" if predicted == stored else "miss"] += 1
        results.append(record)
        stats[f"after_{record['after']}"] += 1
        stats[f"source_{record['source'] or 'none'}"] += 1
        if record["ai_source"]:
            stats[f"ai_{record['ai_source']}"] += 1
        print(json.dumps(record, ensure_ascii=False))

    print("\n=== summary ===")
    print(json.dumps(dict(stats), ensure_ascii=False, indent=2))
    print(f"evaluated={len(results)} skipped_same_dir={stats['skipped_same_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
