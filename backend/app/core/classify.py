"""自动分类引擎：识别整理后目录的已有结构，按作品信息（类型 / 地区 / 题材）匹配分类文件夹。

不依赖某一种命名习惯 —— 关键词表同时覆盖中英文常见写法（如 欧美 / 美国 / western，
日韩 / 日本 / 日漫 / anime，华语 / 国产 / chinese），对候选目录名打分选出最匹配的一个。
识别不到时回退到「其他」类目录；仍然失败则保持默认路径（不加分类前缀）。
"""

from __future__ import annotations

import re
from typing import Any

# ---- 关键词表（可按需扩展；分数代表区分度）----
MOVIE_TERMS: tuple[tuple[str, float], ...] = (
    ("电影", 3.0), ("影片", 3.0), ("映画", 3.0),
    ("movie", 3.0), ("movies", 3.0), ("film", 3.0), ("films", 3.0),
)

TV_TERMS: tuple[tuple[str, float], ...] = (
    ("剧集", 3.0), ("电视剧", 3.0), ("series", 3.0),
    ("tv", 3.0), ("shows", 3.0), ("show", 1.5),
    ("动漫", 2.5), ("番剧", 2.5),
)

# 出现这些词说明目录偏向剧集/动画剧集：对「电影」条目起反向排除作用
TV_MARKERS: tuple[tuple[str, float], ...] = TV_TERMS + (
    ("美剧", 2.5), ("日剧", 2.5), ("韩剧", 2.5), ("英剧", 2.5),
    ("港剧", 2.5), ("台剧", 2.5), ("国产剧", 2.5), ("新番", 2.0),
)

REGION_KEYWORDS: dict[str, tuple[tuple[str, float], ...]] = {
    "cn": (
        ("华语", 3.0), ("国产", 3.5), ("中国", 3.5), ("大陆", 3.5), ("内地", 3.5),
        ("中文", 2.5), ("国漫", 3.0), ("chinese", 3.5), ("china", 3.5),
        ("mainland", 3.5), ("mandarin", 2.5), ("亚洲", 3.0), ("asia", 3.0),
    ),
    "hk": (
        ("港台", 3.0), ("香港", 3.5), ("港剧", 3.5), ("港片", 3.5), ("华语", 2.5),
        ("hongkong", 3.5), ("hong kong", 3.5), ("hk", 2.5), ("亚洲", 3.0), ("asia", 3.0),
    ),
    "tw": (
        ("港台", 3.0), ("台湾", 3.5), ("台剧", 3.5), ("华语", 2.5),
        ("taiwan", 3.5), ("tw", 2.5), ("亚洲", 3.0), ("asia", 3.0),
    ),
    "jp": (
        ("日韩", 3.0), ("日本", 3.5), ("日剧", 3.5), ("日影", 3.5), ("日漫", 3.0),
        ("日系", 3.0), ("和风", 3.0), ("japan", 3.5), ("jp", 2.5), ("亚洲", 3.0), ("asia", 3.0),
    ),
    "kr": (
        ("日韩", 3.0), ("韩国", 3.5), ("韩剧", 3.5), ("韩影", 3.5), ("韩漫", 3.0),
        ("korea", 3.5), ("kr", 2.5), ("亚洲", 3.0), ("asia", 3.0),
    ),
    "us": (
        ("欧美", 3.0), ("美国", 3.5), ("美剧", 3.5), ("美影", 3.5), ("美漫", 3.0),
        ("英美", 3.0), ("好莱坞", 3.5), ("western", 3.0), ("american", 3.5),
        ("hollywood", 3.5), ("usa", 3.0),
    ),
    "eu": (
        ("欧美", 3.0), ("欧洲", 3.5), ("英国", 3.5), ("英剧", 3.5), ("英影", 3.5),
        ("德国", 3.5), ("法国", 3.5), ("欧影", 3.0), ("western", 3.0),
        ("europe", 3.0), ("european", 3.0), ("british", 3.5), ("uk", 2.5),
    ),
}

GENRE_KEYWORDS: dict[str, tuple[tuple[str, float], ...]] = {
    "animation": (
        ("动画", 4.0), ("动漫", 4.0), ("anime", 4.0), ("animation", 4.0),
        ("cartoon", 3.5), ("番剧", 3.5), ("新番", 3.5),
        ("日漫", 3.0), ("国漫", 3.0), ("欧美漫", 3.0), ("美漫", 3.0),
    ),
    "documentary": (
        ("纪录片", 4.0), ("纪录", 4.0), ("纪实", 3.5), ("documentary", 4.0), ("docu", 3.0),
    ),
    "variety": (
        ("综艺", 4.0), ("真人秀", 4.0), ("脱口秀", 3.5), ("variety", 3.5), ("talk show", 3.0),
    ),
}

FALLBACK_KEYWORDS = ("其他", "其它", "other", "others", "misc")

# 阈值：一级目录至少要命中类型词；二级目录需要额外命中地区或题材
TOP_MIN_SCORE = 3.0
SUB_MIN_SCORE = 3.0

_CJK_LANGUAGES = {"zh", "cn", "yue"}
_EU_LANGUAGES = {
    "fr", "de", "es", "it", "ru", "pt", "nl", "sv", "da", "no", "fi",
    "pl", "tr", "el", "cs", "hu",
}
_EU_COUNTRIES = {
    "GB", "UK", "FR", "DE", "ES", "IT", "RU", "PT", "NL", "SE", "DK", "NO",
    "FI", "PL", "CA", "AU", "NZ", "IE", "BE", "CH", "AT", "CZ", "GR", "HU",
    "RO", "UA", "TR", "BR", "MX", "AR",
}


def _all_keywords() -> set[str]:
    keywords: set[str] = set()
    for table in (MOVIE_TERMS, TV_MARKERS):
        keywords.update(keyword.lower() for keyword, _ in table)
    for table in (REGION_KEYWORDS, GENRE_KEYWORDS):
        for terms in table.values():
            keywords.update(keyword.lower() for keyword, _ in terms)
    return keywords


_ALL_KEYWORDS = _all_keywords()


def _compact(name: str) -> str:
    return re.sub(r"[\s\-_.·&+]+", "", (name or "").lower())


def _exact_bonus(name: str) -> float:
    """目录名恰好等于某个关键词时给小加成，用于打破平局（如 电影 vs 特摄电影）。"""
    return 0.4 if _compact(name) in _ALL_KEYWORDS else 0.0


def _hits(name: str, keywords: tuple[tuple[str, float], ...]) -> float:
    lowered = (name or "").lower()
    best = 0.0
    for keyword, weight in keywords:
        key = keyword.lower()
        if not key:
            continue
        if key.isascii():
            if len(key) <= 3:
                # 短代号按单词边界匹配，避免 us/uk/tv 误伤（如 museum 里的 us）
                if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", lowered):
                    best = max(best, weight)
            elif key in lowered:
                best = max(best, weight)
        elif key in lowered:
            best = max(best, weight)
    return best


def _has_genre(merged: dict[str, Any], genre_id: int, names: tuple[str, ...] = ()) -> bool:
    for value in merged.get("genre_ids") or []:
        try:
            if int(value) == genre_id:
                return True
        except (TypeError, ValueError):
            continue
    for label in merged.get("genres") or []:
        text = str(label).lower()
        if any(name.lower() in text for name in names):
            return True
    return False


def attributes_for(match: dict[str, Any] | None, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """从匹配信息（含新扫描存的字段与可选详情）提炼分类属性。"""
    merged: dict[str, Any] = {**(match or {}), **(details or {})}
    media_type = merged.get("media_type") or "movie"
    language = str(merged.get("original_language") or "").strip().lower()

    countries: set[str] = set()
    for value in merged.get("origin_country") or []:
        if value:
            countries.add(str(value).upper())
    for value in merged.get("production_countries") or []:
        if isinstance(value, dict) and value.get("iso_3166_1"):
            countries.add(str(value["iso_3166_1"]).upper())
        elif isinstance(value, str) and value:
            countries.add(value.upper())

    regions: set[str] = set()
    if countries:
        if "CN" in countries:
            regions.add("cn")
        if "HK" in countries:
            regions.add("hk")
        if "TW" in countries:
            regions.add("tw")
        if "JP" in countries:
            regions.add("jp")
        if "KR" in countries:
            regions.add("kr")
        if "US" in countries:
            regions.add("us")
        if countries & _EU_COUNTRIES:
            regions.add("eu")
    if not regions:
        if language in ("zh", "cn"):
            regions.add("cn")
        elif language == "yue":
            regions.add("hk")
        elif language == "ja":
            regions.add("jp")
        elif language == "ko":
            regions.add("kr")
        elif language == "en":
            regions.update({"us", "eu"})
        elif language in _EU_LANGUAGES:
            regions.add("eu")

    genres: set[str] = set()
    if _has_genre(merged, 16, ("动画", "Animation")):
        genres.add("animation")
    if _has_genre(merged, 99, ("纪录", "Documentary")):
        genres.add("documentary")
    if media_type == "tv":
        variety = {
            10763: ("新闻", "News"),
            10764: ("真人秀", "Reality"),
            10766: ("肥皂剧", "Soap"),
            10767: ("脱口秀", "Talk"),
        }
        for genre_id, names in variety.items():
            if _has_genre(merged, genre_id, names):
                genres.add("variety")
                break

    return {"media_type": media_type, "language": language, "countries": countries, "regions": regions, "genres": genres}


def score_folder(name: str, attr: dict[str, Any]) -> dict[str, float]:
    """给单个目录名打分。total = 类型 + 地区 + 题材 + 精确加成 - 反类型惩罚。"""
    media_type = attr.get("media_type") or "movie"
    if media_type == "movie":
        own = _hits(name, MOVIE_TERMS)
        penalty = 3.0 if own == 0 and _hits(name, TV_MARKERS) > 0 else 0.0
    else:
        own = _hits(name, TV_TERMS)
        penalty = 3.0 if own == 0 and _hits(name, MOVIE_TERMS) > 0 else 0.0

    region = 0.0
    for region_id in attr.get("regions") or ():
        terms = REGION_KEYWORDS.get(region_id)
        if terms:
            region = max(region, _hits(name, terms))

    genre = 0.0
    for genre_id in attr.get("genres") or ():
        terms = GENRE_KEYWORDS.get(genre_id)
        if terms:
            genre += _hits(name, terms)

    bonus = _exact_bonus(name)
    total = own + region + genre + bonus - penalty
    return {"total": round(total, 3), "type": own, "region": region, "genre": genre, "bonus": bonus}


def is_fallback_name(name: str) -> bool:
    """名字里含 其他/other 等关键词的目录（作为候选时降权跳过，仅用于兜底）。"""
    lowered = (name or "").lower()
    return any(keyword in lowered for keyword in FALLBACK_KEYWORDS)


def best_folder(
    names: list[str],
    attr: dict[str, Any],
    *,
    min_score: float = TOP_MIN_SCORE,
    require_region_or_genre: bool = False,
) -> tuple[str, dict[str, float]]:
    """从候选目录名中选出得分最高者；没有达标返回 ("", {})。"""
    best_name = ""
    best_parts: dict[str, float] = {}
    for name in names:
        if is_fallback_name(name):
            continue
        parts = score_folder(name, attr)
        if require_region_or_genre and parts["region"] <= 0 and parts["genre"] <= 0:
            continue
        if parts["total"] >= min_score and parts["total"] > best_parts.get("total", -999.0):
            best_name, best_parts = name, parts
    return best_name, best_parts


def pick_other(names: list[str]) -> str:
    """回退目录：名字里含 其他/other 等关键词的文件夹。"""
    for name in names:
        if is_fallback_name(name):
            return name
    return ""


def needs_details(match: dict[str, Any] | None) -> bool:
    """旧数据缺分类所需字段（语言/题材）时，调用方应补充获取详情。"""
    match = match or {}
    if not match.get("original_language"):
        return True
    return not match.get("genre_ids") and not match.get("genres")
