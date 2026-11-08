"""AI 联网识别、两轮候选挑选、AI 选目录的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_ai_features.py
使用临时数据库与临时媒体目录，不访问真实 AI / TMDb。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DATA_DIR = tempfile.mkdtemp(prefix="cinemint-ai-")
MEDIA_DIR = tempfile.mkdtemp(prefix="cinemint-ai-media-")
os.environ["CINEMINT_DATA_DIR"] = DATA_DIR
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from app import db  # noqa: E402

db.init_db()

import app.core.ai_organize as ai_organize  # noqa: E402
import app.core.metadata.ai as ai  # noqa: E402
from app.core.naming import DEFAULT_NAMING, DEFAULT_SCRAPE  # noqa: E402
from app.core.organize_targets import load_targets  # noqa: E402
from app.core.providers.local import LocalFileSource  # noqa: E402
from app.db import dumps, execute, now, query_one, set_setting  # noqa: E402


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


class FakeResponse:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)[:300]

    def json(self):
        return self._body


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    ai.ai_available = lambda: True
    ai._ai_config = lambda: ("http://fake/v1", "fake-model", "sk-test")
    saved_post = ai.httpx.post

    def web_body() -> dict:
        return {
            "output": [
                {
                    "type": "web_search_call",
                    "action": {"sources": [{"url": "https://example.com/a"}]},
                },
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"title":"假面骑士Myth","original_title":"Kamen Rider Myth",'
                            '"aliases":["Kamen Rider My-Th"],"year":2026,"media_type":"tv",'
                            '"season":1,"episode":1,"confidence":0.9,"reason":"联网确认"}',
                        }
                    ],
                },
            ],
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }

    def fake_post(url, headers=None, json=None, timeout=None):
        if url.endswith("/responses"):
            return FakeResponse(200, web_body())
        return FakeResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"title":"普通对话","aliases":[],"confidence":0.5,'
                            '"reason":"chat"}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    ai.httpx.post = fake_post
    ai._WEB_SEARCH_DISABLED.clear()
    hint = ai.parse_with_ai("Kamen.Rider.Myth.S01E01.mkv", "Myth")
    expect("web hint source", hint.get("source"), "web_search")
    expect("web hint title", hint.get("title"), "假面骑士Myth")
    expect("web hint sources", hint.get("sources"), ["https://example.com/a"])

    # 网关不支持 /responses 时回退普通对话
    def no_responses(url, headers=None, json=None, timeout=None):
        if url.endswith("/responses"):
            return FakeResponse(404, {"error": "not found"})
        return fake_post(url, headers, json, timeout)

    ai.httpx.post = no_responses
    ai._WEB_SEARCH_DISABLED.clear()
    hint = ai.parse_with_ai("x.mkv", "")
    expect("web fallback source", hint.get("source"), "chat")
    expect("web fallback flag", "联网搜索不可用" in (hint.get("ai_error") or ""), True)

    # 两轮候选挑选
    def pick_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"index":1,"confidence":0.88,"aliases":["Myth"],'
                            '"reason":"年份与原名一致"}'
                        }
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10},
            },
        )

    ai.httpx.post = pick_post
    picked = ai.pick_candidate(
        "x.mkv",
        "dir",
        [
            {"title": "A", "original_title": "", "year": 2020, "media_type": "tv"},
            {"title": "假面骑士Myth", "original_title": "Kamen Rider Myth", "year": 2026, "media_type": "tv"},
        ],
    )
    expect("pick index", picked.get("index"), 1)
    expect("pick confidence", picked.get("confidence"), 0.88)
    expect("pick aliases", picked.get("aliases"), ["Myth"])
    ai.httpx.post = saved_post

    # ---- AI 选目录：结构快照 + 只能选现有目录 ----
    media = Path(MEDIA_DIR)
    target = media / "library"
    (target / "电影" / "欧美电影").mkdir(parents=True)
    (target / "动漫" / "日漫").mkdir(parents=True)
    source = LocalFileSource({"base_path": str(media)})
    source_id = execute(
        "INSERT INTO sources(name, type, config, created_at, updated_at) VALUES(?,?,?,?,?)",
        ("本地", "local", dumps({"base_path": str(media)}), now(), now()),
    )
    library_id = execute(
        "INSERT INTO libraries(source_id, name, root_path, target_root, media_type, naming, scrape, "
        "enabled, is_demo, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            "AI 目录测试库",
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
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    set_setting("ai_organize_mode", "suggest")
    structure = ai_organize.build_structure(library_id, source, "library", refresh=True)
    expect("structure paths", set(structure["paths"]), {"电影", "电影/欧美电影", "动漫", "动漫/日漫"})
    expect("structure file", ai_organize._structure_file(library_id).is_file(), True)

    match = {"tmdb_id": 999, "media_type": "tv", "title": "假面骑士Myth", "year": 2026}

    ai_organize.chat_json = lambda *a, **k: {
        "path": "动漫/日漫",
        "confidence": 0.9,
        "reason": "动画分类",
    }
    suggestion = ai_organize.suggest_category(
        library, source, match, {"genres": [{"name": "动画"}]}, structure=structure
    )
    expect("suggest path", suggestion["path"], "动漫/日漫")
    expect("suggest confidence", suggestion["confidence"], 0.9)
    expect("suggest source", suggestion["source"], "ai")

    ai_organize.chat_json = lambda *a, **k: {
        "path": "动漫/不存在",
        "confidence": 0.99,
        "reason": "越界",
    }
    expect(
        "suggest rejects unknown path",
        ai_organize.suggest_category(library, source, match, {}, structure=structure),
        None,
    )

    ai_organize.chat_json = lambda *a, **k: {
        "path": "动漫/日漫",
        "confidence": 0.9,
        "reason": "动画分类",
    }
    saved = ai_organize.apply_suggestion(library, match, suggestion)
    expect("apply source", saved["source"], "ai")
    row = query_one(
        "SELECT source, confidence, reason FROM organize_targets WHERE library_id=?",
        (library_id,),
    )
    expect("db source", row["source"], "ai")
    expect("db confidence", row["confidence"], 0.9)
    expect("db reason", row["reason"], "动画分类")
    targets = load_targets(library_id)
    expect("load targets source", targets["tv:999"]["source"], "ai")

    # 手动保存会覆盖来源
    from app.core.organize_targets import save_target

    save_target(library_id, "tv:999", "电影/欧美电影", "category")
    row = query_one(
        "SELECT source, confidence FROM organize_targets WHERE library_id=?", (library_id,)
    )
    expect("manual overrides source", row["source"], "manual")
    expect("manual clears confidence", row["confidence"], None)

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
