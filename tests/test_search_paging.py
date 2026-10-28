"""TMDb 手动搜索翻页的回归测试。

覆盖：
- TMDBClient.search_page 把 page 传给 TMDb，并解析 page / total_pages；
- candidates_from_search_page 保留分页信息、去重、兼容只实现 search 的替身；
- POST /api/items/search 与 POST /api/items/{id}/search 支持 page 参数。

运行：cd backend && .venv/bin/python ../tests/test_search_paging.py
使用临时数据库，不会访问真实 TMDb 或影视库。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cinemint-search-paging-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="cinemint-search-media-")
os.environ["CINEMINT_DATA_DIR"] = TEMP_DATA
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import dumps, execute, now  # noqa: E402
from app.main import app  # noqa: E402
from app.core.matcher import candidates_from_search_page  # noqa: E402
from app.core.metadata.tmdb import TMDBClient  # noqa: E402
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402

import app.api.items as items_api  # noqa: E402


def candidate(tmdb_id: int, media_type: str, title: str) -> dict:
    return {
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "title": title,
        "original_title": title,
        "year": 2024,
        "overview": "",
        "poster_path": "",
        "backdrop_path": "",
        "logo_path": "",
        "popularity": 1.0,
        "vote_average": 0.0,
        "vote_count": 0,
        "original_language": "zh",
        "genre_ids": [],
        "origin_country": [],
    }


class FakeSearchClient:
    configured = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search_page(self, query: str, media_type: str = "", year=None, page: int = 1) -> dict:
        self.calls.append({"query": query, "media_type": media_type, "year": year, "page": page})
        return {
            "results": [candidate(page * 10 + 1, media_type or "tv", f"结果第{page}页")],
            "page": page,
            "total_pages": 6,
            "total_results": 120,
        }


class DuplicateClient:
    configured = True

    def search_page(self, query: str, media_type: str = "", year=None, page: int = 1) -> dict:
        item = candidate(1, "tv", "重复")
        return {
            "results": [item, dict(item)],
            "page": page,
            "total_pages": 1,
            "total_results": 2,
        }


class SearchOnlyClient:
    def search(self, query: str, media_type: str = "", year=None) -> list[dict]:
        return [candidate(2, "movie", "只有搜索")]


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    # --- TMDBClient.search_page：page 参数与返回分页信息 ---
    tmdb = TMDBClient("test-key")
    calls: dict = {}

    def fake_get(path: str, params: dict | None = None, use_cache: bool = True) -> dict:
        calls["path"] = path
        calls["params"] = dict(params or {})
        return {
            "page": int((params or {}).get("page") or 1),
            "total_pages": 9,
            "total_results": 180,
            "results": [
                {
                    "id": 7,
                    "name": "翻页剧",
                    "original_name": "Paged Show",
                    "first_air_date": "2021-05-01",
                }
            ],
        }

    tmdb._get = fake_get  # type: ignore[method-assign]
    data = tmdb.search_page("翻页", "tv", None, 3)
    expect("tmdb search path", calls["path"], "/search/tv")
    expect("tmdb page param", calls["params"].get("page"), 3)
    expect("tmdb tv includes adult", calls["params"].get("include_adult"), "true")
    tmdb.search_page("翻页", "movie", None, 1)
    expect("tmdb movie path", calls["path"], "/search/movie")
    expect("tmdb movie includes adult", calls["params"].get("include_adult"), "true")
    tmdb.search_page("翻页", "", None, 1)
    expect("tmdb multi path", calls["path"], "/search/multi")
    expect("tmdb multi includes adult", calls["params"].get("include_adult"), "true")
    expect("tmdb page", data["page"], 3)
    expect("tmdb total pages", data["total_pages"], 9)
    expect("tmdb total results", data["total_results"], 180)
    expect("tmdb result title", data["results"][0]["title"], "翻页剧")
    expect("tmdb result type", data["results"][0]["media_type"], "tv")
    empty = tmdb.search_page("", "tv", None, 2)
    expect("tmdb empty query results", empty["results"], [])
    expect("tmdb empty query pages", empty["total_pages"], 0)

    # --- candidates_from_search_page ---
    fake = FakeSearchClient()
    page_data = candidates_from_search_page(fake, "查询", "tv", None, 4)
    expect("candidates call page", fake.calls[0]["page"], 4)
    expect("candidates call type", fake.calls[0]["media_type"], "tv")
    expect("candidates page", page_data["page"], 4)
    expect("candidates total pages", page_data["total_pages"], 6)
    expect("candidates total results", page_data["total_results"], 120)
    expect("candidates result count", len(page_data["results"]), 1)

    dedup = candidates_from_search_page(DuplicateClient(), "x")
    expect("candidates dedupe", len(dedup["results"]), 1)

    fallback = candidates_from_search_page(SearchOnlyClient(), "x")
    expect("candidates fallback page", fallback["page"], 1)
    expect("candidates fallback pages", fallback["total_pages"], 1)
    expect("candidates fallback count", len(fallback["results"]), 1)

    # --- API 翻页参数透传 ---
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cinemint"})
        expect("login", login.status_code, 200)
        source_id = execute(
            "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("翻页测试源", "local", dumps({"base_path": MEDIA_ROOT}), now(), now()),
        )
        library_id = execute(
            "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
            "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "翻页测试库",
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
        item_id = execute(
            "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, candidates, status, "
            "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (library_id, "inbox/x.mkv", "x.mkv", 1, 0.0, dumps({}), dumps([]), "unmatched", now(), now()),
        )

        fake_api = FakeSearchClient()
        original = items_api.client_from_settings
        items_api.client_from_settings = lambda: fake_api
        try:
            any_result = client.post(
                "/api/items/search",
                json={"query": "翻页", "media_type": "tv", "page": 5},
            )
            expect("search any status", any_result.status_code, 200)
            any_body = any_result.json()
            expect("search any page", any_body.get("page"), 5)
            expect("search any total pages", any_body.get("total_pages"), 6)
            expect("search any total results", any_body.get("total_results"), 120)
            expect("search any result count", len(any_body.get("results", [])), 1)
            expect("search any call page", fake_api.calls[-1]["page"], 5)
            expect("search any decorated", "poster_url" in any_body["results"][0], True)

            item_result = client.post(
                f"/api/items/{item_id}/search",
                json={"query": "翻页", "media_type": "tv", "page": 2},
            )
            expect("item search status", item_result.status_code, 200)
            item_body = item_result.json()
            expect("item search page", item_body.get("page"), 2)
            expect("item search total pages", item_body.get("total_pages"), 6)
            expect("item search call page", fake_api.calls[-1]["page"], 2)
            expect("item search decorated", "poster_url" in item_body["results"][0], True)
        finally:
            items_api.client_from_settings = original

    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
