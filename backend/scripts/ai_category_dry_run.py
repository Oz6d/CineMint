"""只读 dry-run：AI 从现有分类里选目录，只打印建议，不写 organize_targets。

用法：PYTHONPATH=/path/to/newapp CINEMINT_DATA_DIR=/data python scripts/ai_category_dry_run.py --limit 6
"""

from __future__ import annotations

import argparse
import json

from app.core.ai_organize import build_structure, suggest_category
from app.core.metadata.tmdb import client_from_settings
from app.core.providers import build_source
from app.db import loads, query_all, query_one


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=int, default=1)
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()

    library = query_one("SELECT * FROM libraries WHERE id=?", (args.library,))
    if not library:
        print("影视库不存在")
        return 1
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    source = build_source(source_row)
    structure = build_structure(args.library, source, library["target_root"] or "", refresh=True)
    print("structure_paths", len(structure["paths"]))
    print("structure_sample", structure["paths"][:12])

    rows = query_all(
        "SELECT match_key, match FROM items WHERE library_id=? AND status='matched' "
        "AND match_key IS NOT NULL AND match_key<>'' AND match_key NOT IN "
        "(SELECT match_key FROM organize_targets WHERE library_id=?) "
        "GROUP BY match_key ORDER BY MAX(id) DESC LIMIT ?",
        (args.library, args.library, max(1, args.limit)),
    )
    client = client_from_settings()
    for row in rows:
        match = loads(row["match"], {}) or {}
        details = {}
        if client.configured and match.get("tmdb_id"):
            try:
                details = client.details(int(match["tmdb_id"]), match.get("media_type") or "movie")
            except Exception:
                details = {}
        suggestion = suggest_category(library, source, match, details, structure=structure)
        print(
            json.dumps(
                {
                    "match_key": row["match_key"],
                    "title": match.get("title"),
                    "year": match.get("year"),
                    "media_type": match.get("media_type"),
                    "suggestion": suggestion,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
