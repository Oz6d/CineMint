"""海报墙真实性校验缓存的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_presence_cache.py
使用临时数据目录和临时本地媒体，不会写 data/cineflow.db。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cineflow-presence-cache-")
MEDIA_ROOT = tempfile.mkdtemp(prefix="cineflow-presence-media-")
os.environ["CINEFLOW_DATA_DIR"] = TEMP_DATA
os.environ["CINEFLOW_DEMO"] = "false"
os.environ["CINEFLOW_ADMIN_PASSWORD"] = "cineflow"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.file_presence import filter_existing  # noqa: E402
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402
from app.core.presence_cache import (  # noqa: E402
    clear_presence_cache,
    get_listing,
    invalidate_library,
)
from app.core.providers.base import Entry, FileSource  # noqa: E402
from app.db import dumps, execute, now  # noqa: E402
from app.main import app  # noqa: E402


class CountingSource(FileSource):
    type = "counting"

    def __init__(self, tree: dict[str, list[str]]):
        super().__init__({})
        self.tree = tree
        self.calls: list[str] = []

    def list(self, path: str = "") -> list[Entry]:
        self.calls.append(path)
        return [
            Entry(name=name, path=f"{path}/{name}".strip("/"), is_dir=False, size=1, mtime=0)
            for name in self.tree.get(path, [])
        ]


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

    # ---- 目录列表缓存：TTL 内复用，主动失效后重新读取 ----
    clear_presence_cache()
    calls: list[int] = []

    def loader() -> set[str]:
        calls.append(1)
        return {"a.mkv"}

    expect("first listing", get_listing(101, "inbox", loader, ttl=60), {"a.mkv"})
    expect("second listing cached", get_listing(101, "inbox", loader, ttl=60), {"a.mkv"})
    expect("loader called once", len(calls), 1)
    invalidate_library(101)
    expect("listing after invalidation", get_listing(101, "inbox", loader, ttl=60), {"a.mkv"})
    expect("loader called again", len(calls), 2)

    # ---- 目录不可访问时缓存失败结果，避免短时间反复请求 ----
    failure_calls: list[int] = []

    def broken_loader() -> set[str]:
        failure_calls.append(1)
        raise RuntimeError("目录不可用")

    expect("failure cached as None", get_listing(102, "broken", broken_loader), None)
    expect("failure cached again", get_listing(102, "broken", broken_loader), None)
    expect("failure loader called once", len(failure_calls), 1)

    # ---- filter_existing 使用 library_id 缓存，同时保持条目实时过滤 ----
    clear_presence_cache()
    source = CountingSource({"inbox": ["a.mkv"], "show": ["e01.mkv"]})
    rows = [
        {"source_path": "inbox/a.mkv"},
        {"source_path": "inbox/b.mkv"},
        {"source_path": "show/e01.mkv"},
        {"source_path": "show/e02.mkv"},
    ]
    present, hidden, unverified = filter_existing(rows, source, library_id=201)
    expect("cached filter present", [row["source_path"] for row in present], ["inbox/a.mkv", "show/e01.mkv"])
    expect("cached filter hidden", hidden, 2)
    expect("cached filter unverified", unverified, 0)
    expect("cached filter each parent once", (source.calls.count("inbox"), source.calls.count("show")), (1, 1))

    present_again, _, _ = filter_existing(rows, source, library_id=201)
    expect("cached filter reused", [row["source_path"] for row in present_again], ["inbox/a.mkv", "show/e01.mkv"])
    expect("cached filter no new requests", (source.calls.count("inbox"), source.calls.count("show")), (1, 1))

    invalidate_library(201)
    filter_existing(rows, source, library_id=201)
    expect("cached filter refreshed", (source.calls.count("inbox"), source.calls.count("show")), (2, 2))

    # ---- 海报墙 API：缓存、refresh 绕过、文件删除后主动失效 ----
    media = Path(MEDIA_ROOT)
    movie_dir = media / "inbox" / "Movie"
    movie_dir.mkdir(parents=True)
    movie_file = movie_dir / "Movie.2023.1080p.mkv"
    movie_file.write_bytes(b"movie")

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cineflow"})
        expect("login", login.status_code, 200)

        source_id = execute(
            "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
            ("缓存测试源", "local", dumps({"base_path": str(media)}), now(), now()),
        )
        library_id = execute(
            "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
            "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                source_id,
                "海报墙缓存测试库",
                "inbox",
                "library",
                "auto",
                dumps(DEFAULT_NAMING),
                dumps(DEFAULT_SCRAPE),
                1,
                0,
                now(),
                now(),
            ),
        )
        execute(
            "INSERT INTO items(library_id, source_path, file_name, size, mtime, parsed, match, status, "
            "match_key, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                library_id,
                "inbox/Movie/Movie.2023.1080p.mkv",
                "Movie.2023.1080p.mkv",
                5,
                0,
                dumps({"title": "Movie", "year": 2023, "media_type": "movie"}),
                dumps({"tmdb_id": 1, "media_type": "movie", "title": "Movie", "year": 2023}),
                "matched",
                "movie:1",
                now(),
                now(),
            ),
        )

        first = client.get(f"/api/libraries/{library_id}/works")
        expect("works first status", first.status_code, 200)
        expect("works first count", len(first.json().get("works") or []), 1)
        first_work = (first.json().get("works") or [{}])[0]
        expect(
            "works planned name",
            (first_work.get("items") or [{}])[0].get("planned_name"),
            "Movie (2023) 1080p.mkv",
        )

        movie_file.unlink()
        cached = client.get(f"/api/libraries/{library_id}/works")
        expect("works cached within ttl", len(cached.json().get("works") or []), 1)

        refreshed = client.get(f"/api/libraries/{library_id}/works?refresh=1")
        expect("works refresh bypasses cache", len(refreshed.json().get("works") or []), 0)
        expect("works refresh hidden missing", refreshed.json().get("hidden_missing"), 1)

        movie_file.write_bytes(b"movie")
        refreshed_again = client.get(f"/api/libraries/{library_id}/works?refresh=1")
        expect("works refresh sees restored file", len(refreshed_again.json().get("works") or []), 1)

        deleted = client.post(
            f"/api/libraries/{library_id}/files/delete",
            json={"paths": ["Movie/Movie.2023.1080p.mkv"]},
        )
        expect("file delete status", deleted.status_code, 200)
        after_delete = client.get(f"/api/libraries/{library_id}/works")
        expect("file delete invalidates works cache", len(after_delete.json().get("works") or []), 0)

    clear_presence_cache()
    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
