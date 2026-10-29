"""同名文件处理策略回归测试。

覆盖影视库级 / 订阅级 collision 配置，以及整理时的 suffix / skip / overwrite。
运行：cd backend && .venv/bin/python ../tests/test_collision.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE, normalize_collision
from app.core.organizer import Organizer
from app.core.providers.local import LocalFileSource
from app.core.rss import normalize_config as normalize_rss_config
from app.core.subscriptions import (
    SubscriptionConfigError,
    _reconcile_organized_entry,
    normalize_config,
)
from collections import defaultdict


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def run_policy(policy: str) -> dict:
    root = Path(tempfile.mkdtemp(prefix=f"cinemint-collision-{policy}-"))
    inbox = root / "inbox"
    target_dir = root / "library" / "Movie (2024)"
    inbox.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    source_path = "inbox/Movie.2024.mkv"
    (root / source_path).write_bytes(b"new")
    target_file = target_dir / "Movie (2024) 1080p.mkv"
    target_file.write_bytes(b"old")
    source = LocalFileSource({"base_path": str(root)})
    organizer = Organizer(
        source,
        naming={**DEFAULT_NAMING, "collision": policy, "auto_category": False},
        scrape={**DEFAULT_SCRAPE, "enabled": False},
        source_root="inbox",
    )
    parsed = {"title": "Movie", "year": 2024, "media_type": "movie", "resolution": "1080p"}
    match = {"title": "Movie", "year": 2024, "media_type": "movie", "tmdb_id": 1}
    result = organizer.organize(
        source_path=source_path,
        parsed=parsed,
        match=match,
        target_root="library",
        collision=policy,
    )
    return {
        "result": result,
        "source_exists": (root / source_path).exists(),
        "target": target_file,
        "target_one": target_dir / "Movie (2024) 1080p (1).mkv",
    }


def main() -> int:
    passed = total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    expect("normalize suffix", normalize_collision("SUFFIX"), "suffix")
    expect("normalize invalid", normalize_collision("bad"), "suffix")
    expect("normalize skip", normalize_collision("skip"), "skip")
    expect("normalize overwrite", normalize_collision("overwrite"), "overwrite")

    skipped = run_policy("skip")
    expect("skip returns skipped", skipped["result"].skipped, True)
    expect("skip keeps source", skipped["source_exists"], True)
    expect("skip keeps old target", skipped["target"].read_bytes(), b"old")
    expect("skip no (1)", skipped["target_one"].exists(), False)

    suffixed = run_policy("suffix")
    expect("suffix not skipped", suffixed["result"].skipped, False)
    expect("suffix moves source", suffixed["source_exists"], False)
    expect("suffix keeps old target", suffixed["target"].read_bytes(), b"old")
    expect("suffix creates (1)", suffixed["target_one"].read_bytes(), b"new")

    overwritten = run_policy("overwrite")
    expect("overwrite not skipped", overwritten["result"].skipped, False)
    expect("overwrite source moved", overwritten["source_exists"], False)
    expect("overwrite target content", overwritten["target"].read_bytes(), b"new")
    expect("overwrite no (1)", overwritten["target_one"].exists(), False)

    quark = normalize_config({"share_url": "https://pan.quark.cn/s/abcdef", "collision": "skip"}, "订阅")
    expect("quark collision", quark.get("collision"), "skip")
    rss = normalize_rss_config(
        {"feed_urls": ["https://example.com/feed.xml"], "collision": "overwrite"}, "订阅"
    )
    expect("rss collision", rss.get("collision"), "overwrite")
    try:
        normalize_config({"share_url": "https://pan.quark.cn/s/abcdef", "collision": "bad"}, "订阅")
    except SubscriptionConfigError:
        expect("invalid quark collision rejected", True, True)
    else:
        expect("invalid quark collision rejected", False, True)

    # 订阅条目关联的条目已被手动整理：下次运行直接同步为 organized，不再反复失败/推送。
    import app.core.subscriptions as subscription_module

    original_query = subscription_module.query_one
    original_set = subscription_module._set_entry
    calls: dict = {}

    def fake_query(sql: str, params=()):
        return {"status": "organized", "target_path": "library/Target.mkv"}

    def fake_set(entry_id: int, **fields):
        calls.update({"id": entry_id, **fields})

    subscription_module.query_one = fake_query
    subscription_module._set_entry = fake_set
    try:
        stats = defaultdict(int)
        expect(
            "reconcile organized entry",
            _reconcile_organized_entry({"id": 7, "item_id": 9}, stats),
            True,
        )
        expect("reconcile entry status", calls.get("status"), "organized")
        expect("reconcile entry target", calls.get("target_path"), "library/Target.mkv")
        expect("reconcile skipped stat", stats["skipped"], 1)
        expect(
            "reconcile missing item id",
            _reconcile_organized_entry({"id": 8}, stats),
            False,
        )
    finally:
        subscription_module.query_one = original_query
        subscription_module._set_entry = original_set

    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
