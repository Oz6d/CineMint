"""自动分类引擎的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_classify.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.classify import (  # noqa: E402
    attributes_for,
    best_folder,
    needs_details,
    pick_other,
    score_folder,
)
from app.core.naming import DEFAULT_NAMING, build_target  # noqa: E402
from app.core.organizer import Organizer  # noqa: E402
from app.core.providers.base import Entry, FileSource, join, norm  # noqa: E402


class FakeTreeSource(FileSource):
    """按预设目录树返回列表结果的假文件源。"""

    type = "fake"

    def __init__(self, tree: dict[str, list[str]]):
        super().__init__({})
        self.tree = {norm(path): list(names) for path, names in tree.items()}

    def list(self, path: str = "") -> list[Entry]:
        names = self.tree.get(norm(path), [])
        return [Entry(name=name, path=join(path, name), is_dir=True, size=0, mtime=0) for name in names]


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def match_of(
    media_type: str,
    lang: str = "",
    *,
    animation: bool = False,
    documentary: bool = False,
    variety: bool = False,
    countries: list[str] | None = None,
) -> dict:
    match: dict = {"media_type": media_type, "title": "Test", "original_language": lang}
    genre_ids: list[int] = []
    if animation:
        genre_ids.append(16)
    if documentary:
        genre_ids.append(99)
    if variety:
        genre_ids.append(10764)
    if genre_ids:
        match["genre_ids"] = genre_ids
    if countries:
        match["origin_country"] = countries
    return match


CN_TREE = {
    "For infuse": ["电影", "剧集", "动漫", "综艺", "纪录片", "特摄", "其他"],
    "For infuse/电影": ["动画电影", "欧美电影", "日韩电影", "华语电影", "其他电影"],
    "For infuse/剧集": ["欧美剧", "大陆剧", "日韩剧", "港台剧", "其他剧集"],
    "For infuse/动漫": ["日漫", "国漫", "欧美漫", "其他动漫"],
    "For infuse/综艺": ["日韩综艺", "欧美综艺", "大陆综艺", "其他综艺"],
    "For infuse/纪录片": ["欧美纪录片", "日韩纪录片", "港台纪录片", "大陆纪录片", "其他纪录片"],
}

EN_TREE = {
    "Media": ["Movies", "TV Shows"],
    "Media/Movies": ["Western", "Asian"],
    "Media/TV Shows": [],
}


def main() -> int:
    passed = 0
    total = 0

    organizer_cn = Organizer(FakeTreeSource(CN_TREE), {**DEFAULT_NAMING, "auto_category": True}, {})
    organizer_en = Organizer(FakeTreeSource(EN_TREE), {**DEFAULT_NAMING, "auto_category": True}, {})

    # ---- 中文结构：电影按语言/地区归类 ----
    cases = [
        ("movie en 欧美", match_of("movie", "en"), organizer_cn, "电影/欧美电影"),
        ("movie zh 华语", match_of("movie", "zh"), organizer_cn, "电影/华语电影"),
        ("movie ja 日韩", match_of("movie", "ja"), organizer_cn, "电影/日韩电影"),
        ("movie ja 动画优先", match_of("movie", "ja", animation=True), organizer_cn, "电影/动画电影"),
        ("movie unknown 其他", match_of("movie"), organizer_cn, "电影/其他电影"),
        ("tv zh 大陆", match_of("tv", "zh"), organizer_cn, "剧集/大陆剧"),
        ("tv en 欧美", match_of("tv", "en"), organizer_cn, "剧集/欧美剧"),
        ("tv yue 港台", match_of("tv", "yue"), organizer_cn, "剧集/港台剧"),
        ("tv ja 动画进动漫", match_of("tv", "ja", animation=True), organizer_cn, "动漫/日漫"),
        ("tv zh 动画进国漫", match_of("tv", "zh", animation=True), organizer_cn, "动漫/国漫"),
        ("movie documentary 纪录片", match_of("movie", "en", documentary=True), organizer_cn, "纪录片/欧美纪录片"),
        ("tv documentary 纪录片", match_of("tv", "ja", documentary=True), organizer_cn, "纪录片/日韩纪录片"),
        ("tv variety 综艺", match_of("tv", "zh", variety=True), organizer_cn, "综艺/大陆综艺"),
    ]
    for name, match, organizer, expected in cases:
        total += 1
        passed += check(name, organizer.category_prefix(match, "For infuse"), expected)

    # ---- 英文结构：Movies/Western、Movies/Asian ----
    total += 1
    passed += check("en-structure movie us", organizer_en.category_prefix(match_of("movie", "en"), "Media"), "Movies/Western")
    total += 1
    passed += check("en-structure movie ja", organizer_en.category_prefix(match_of("movie", "ja"), "Media"), "Movies/Asian")
    total += 1
    passed += check("en-structure tv", organizer_en.category_prefix(match_of("tv", "en"), "Media"), "TV Shows")

    # ---- details 补充字段（旧数据缺语言时用详情） ----
    old_match = {"media_type": "movie", "title": "Lord of War", "tmdb_id": 1830}
    details = {"media_type": "movie", "original_language": "en", "production_countries": ["US"], "genres": []}
    total += 1
    passed += check("needs_details", needs_details(old_match), True)
    total += 1
    passed += check(
        "details 决定分类",
        organizer_cn.category_prefix(old_match, "For infuse", details=details),
        "电影/欧美电影",
    )

    # ---- 关闭开关 / 无结构可识别 ----
    organizer_off = Organizer(FakeTreeSource(CN_TREE), {**DEFAULT_NAMING, "auto_category": False}, {})
    total += 1
    passed += check("auto_category off", organizer_off.category_prefix(match_of("movie", "en"), "For infuse"), "")
    organizer_flat = Organizer(FakeTreeSource({"Downloads": ["临时"]}), {**DEFAULT_NAMING, "auto_category": True}, {})
    total += 1
    passed += check("no matching structure", organizer_flat.category_prefix(match_of("movie", "en"), "Downloads"), "")

    # ---- 结构识别输出 ----
    total += 1
    tree = organizer_cn.detect_structure("For infuse")
    passed += check("detect structure names", {node["name"] for node in tree}, {"电影", "剧集", "动漫", "综艺", "纪录片", "特摄", "其他"})
    total += 1
    passed += check(
        "detect structure children",
        sorted(next(node["children"] for node in tree if node["name"] == "电影")),
        sorted(["动画电影", "欧美电影", "日韩电影", "华语电影", "其他电影"]),
    )

    # ---- 打分与回退的单元检查 ----
    attr = attributes_for(match_of("movie", "en"))
    total += 1
    passed += check("score 欧美电影", score_folder("欧美电影", attr)["total"] > score_folder("日韩电影", attr)["total"], True)
    total += 1
    passed += check("best_folder 阈值", best_folder(["办公", "备份"], attr)[0], "")
    total += 1
    passed += check("pick_other", pick_other(["动画电影", "其他电影"]), "其他电影")

    # ---- 多级文件夹模板（与自动分类无关，顺带回归） ----
    folder, _ = build_target(
        {"title": "Lord of War", "year": 2005, "media_type": "movie"},
        {"title": "战争之王", "original_title": "Lord of War", "year": 2005, "media_type": "movie"},
        {**DEFAULT_NAMING, "movie_folder": "电影/欧美电影/{title} ({year})"},
        extension=".mkv",
    )
    total += 1
    passed += check("multi-level folder template", folder, "电影/欧美电影/战争之王 (2005)")

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
