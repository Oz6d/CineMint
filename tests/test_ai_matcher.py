"""AI 识别接入 matcher 的回归测试。

覆盖：
- 解析标题为空 / 候选不足时调用 AI；
- AI 返回标题 + 别名后重新搜索 TMDb；
- 同一目录只调用一次 AI（缓存）；
- AI 找到 TMDb 候选时参与评分；TMDb 仍无结果时进入待确认。
运行：cd backend && .venv/bin/python ../tests/test_ai_matcher.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.matcher import _AI_HINT_CACHE, match_item  # noqa: E402
from app.core.parser import ParsedName  # noqa: E402


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


class FakeTMDB:
    configured = True

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, media_type: str = "", year: int | None = None):
        self.queries.append(query)
        if "Kakusei" in query:
            return [
                {
                    "tmdb_id": 42,
                    "media_type": "tv",
                    "title": "Kakusei Hunter Omegahorn",
                    "original_title": "",
                    "year": 2026,
                    "popularity": 10,
                }
            ]
        return []


def make_parsed() -> ParsedName:
    parsed = ParsedName(raw="[DAY×FSD][UNKNOWN TITLE][15][WEBrip][1080P].mp4")
    parsed.title = ""
    parsed.title_alt = ""
    parsed.episode = 15
    parsed.media_type = "tv"
    parsed.confidence = 0.2
    return parsed


def main() -> int:
    passed = total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    _AI_HINT_CACHE.clear()
    client = FakeTMDB()
    hint = {
        "title": "Kakusei Hunter Omegahorn",
        "title_alt": "",
        "aliases": ["Kakusei Hunter Omegahorn"],
        "year": 2026,
        "media_type": "tv",
        "season": None,
        "episode": 15,
        "confidence": 0.9,
        "reason": "test",
    }
    with (
        patch("app.core.matcher.ai_available", return_value=True),
        patch("app.core.matcher.parse_with_ai", return_value=hint) as ai_mock,
    ):
        result = match_item(
            make_parsed(),
            None,
            client,
            folder_path="share/【FSD】假面骑士麦斯",
        )
        expect("ai query used", "Kakusei Hunter Omegahorn" in client.queries, True)
        expect("ai source", result.get("source"), "ai")
        expect("ai matched", result.get("status"), "matched")
        expect("ai result exposed", (result.get("ai") or {}).get("title"), "Kakusei Hunter Omegahorn")

        second = match_item(
            make_parsed(),
            None,
            client,
            folder_path="share/【FSD】假面骑士麦斯",
        )
        expect("ai cached result", second.get("status"), "matched")
        expect("ai called once per folder", ai_mock.call_count, 1)

        _AI_HINT_CACHE.clear()
        no_match = FakeTMDB()
        with (
            patch("app.core.matcher.ai_available", return_value=True),
            patch("app.core.matcher.parse_with_ai", return_value={**hint, "title": "Unknown Show", "aliases": []}),
        ):
            result = match_item(
                make_parsed(),
                None,
                no_match,
                folder_path="share/unknown",
            )
        expect("ai no tmdb review", result.get("status"), "review")
        expect("ai suggestion in error", "AI 建议" in (result.get("error") or ""), True)

    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
