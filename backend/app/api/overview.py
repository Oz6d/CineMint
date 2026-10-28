from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import Query

from ..config import VERSION
from ..db import get_setting, loads, query_all, query_one
from ..core.metadata.art import backdrop_svg, poster_svg
from .deps import require_auth
from .demo import clear_demo, seed_demo
from .libraries import decorate_match, serialize_library
from .tasks import task_summary

router = APIRouter(tags=["overview"])


@router.get("/api/health")
def health():
    return {
        "ok": True,
        "app": "CineMint",
        "version": VERSION,
        "tmdb_configured": bool(get_setting("tmdb_api_key", "")),
        "demo": bool(get_setting("demo_seeded", False)),
    }


@router.get("/api/overview", dependencies=[Depends(require_auth)])
def overview():
    libraries = [
        serialize_library(row) for row in query_all("SELECT * FROM libraries ORDER BY id ASC")
    ]
    totals = {
        "libraries": len(libraries),
        "items": 0,
        "works": 0,
        "matched": 0,
        "review": 0,
        "unmatched": 0,
        "organized": 0,
        "failed": 0,
        "pending": 0,
    }
    works_seen: set[str] = set()
    for library in libraries:
        counts = library["counts"]
        totals["items"] += counts["total"]
        totals["matched"] += counts["matched"]
        totals["review"] += counts["review"]
        totals["unmatched"] += counts["unmatched"]
        totals["organized"] += counts["organized"]
        totals["failed"] += counts["failed"]
        totals["pending"] += counts["pending"]
        for row in query_all(
            "SELECT DISTINCT match_key FROM items WHERE library_id=? AND match_key IS NOT NULL "
            "AND status NOT IN ('organized','missing','ignored')",
            (library["id"],),
        ):
            works_seen.add(row["match_key"])
    totals["works"] = len(works_seen)

    active = [
        task_summary(row)
        for row in query_all(
            "SELECT * FROM tasks WHERE status IN ('queued','running') ORDER BY id ASC"
        )
    ]
    recent = [
        task_summary(row)
        for row in query_all("SELECT * FROM tasks ORDER BY id DESC LIMIT 6")
    ]

    pending_rows = query_all(
        "SELECT items.*, libraries.name AS library_name FROM items "
        "JOIN libraries ON libraries.id = items.library_id "
        "WHERE items.status IN ('review','unmatched','failed') AND items.is_demo IN (0,1) "
        "ORDER BY CASE items.status WHEN 'failed' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, items.updated_at DESC "
        "LIMIT 8"
    )
    pending = []
    for row in pending_rows:
        match = decorate_match(loads(row["match"], None) if row["match"] else None)
        pending.append(
            {
                "id": row["id"],
                "library_id": row["library_id"],
                "library_name": row["library_name"],
                "file_name": row["file_name"],
                "status": row["status"],
                "error": row["error"],
                "match": match,
                "parsed": loads(row["parsed"], {}) or {},
            }
        )

    return {
        "totals": totals,
        "libraries": libraries,
        "active_tasks": active,
        "recent_tasks": recent,
        "pending_items": pending,
        "demo": bool(get_setting("demo_seeded", False)),
    }


@router.post("/api/demo/seed", dependencies=[Depends(require_auth)])
def demo_seed():
    return seed_demo()


@router.delete("/api/demo", dependencies=[Depends(require_auth)])
def demo_clear():
    return clear_demo()


def _svg_response(content: str) -> Response:
    return Response(
        content=content,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/demo/art/poster/{slug}")
def demo_poster(slug: str, t: str = Query(default="Demo"), y: str = Query(default="")):
    return _svg_response(poster_svg(t, y, slug))


@router.get("/api/demo/art/backdrop/{slug}")
def demo_backdrop(slug: str, t: str = Query(default="Demo"), y: str = Query(default="")):
    return _svg_response(backdrop_svg(t, f"{slug}-{y}"))
