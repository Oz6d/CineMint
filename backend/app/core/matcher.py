from __future__ import annotations

from typing import Any

from ..db import dumps, execute, get_setting, loads, now, query_one
from .metadata.ai import AIError, ai_available, parse_with_ai
from .metadata.tmdb import TMDBClient, TMDBError, normalize_candidate
from .parser import ParsedName, normalize_title, parse_media_name, title_similarity

# 过短的标题（如「Up」「It」）容易误伤其他作品，不参与别名学习。
ALIAS_MIN_LENGTH = 4


def _title_fields(parsed: ParsedName | dict[str, Any]) -> tuple[str, str]:
    if isinstance(parsed, dict):
        return str(parsed.get("title") or ""), str(parsed.get("title_alt") or "")
    return str(getattr(parsed, "title", "") or ""), str(getattr(parsed, "title_alt", "") or "")


def _parsed_value(parsed: ParsedName | dict[str, Any], key: str) -> Any:
    if isinstance(parsed, dict):
        return parsed.get(key)
    return getattr(parsed, key, None)


def alias_keys(parsed: ParsedName | dict[str, Any]) -> list[str]:
    keys: list[str] = []
    title, title_alt = _title_fields(parsed)
    for text in (title, title_alt):
        key = normalize_title(text)
        if len(key) >= ALIAS_MIN_LENGTH and key not in keys:
            keys.append(key)
    return keys


def record_alias(parsed: ParsedName | dict[str, Any], match: dict[str, Any] | None) -> None:
    """记住手动确认的作品，让同一标题的后续文件绕过 TMDb 搜索直接匹配。"""
    if not match or not match.get("tmdb_id") or not match.get("media_type"):
        return
    keys = alias_keys(parsed)
    if not keys:
        return
    payload = {
        key: value
        for key, value in match.items()
        if key not in {"manual", "confidence", "score", "ratio_to_top1", "query"}
    }
    year = payload.get("year") if isinstance(payload.get("year"), int) else None
    display_title = str(payload.get("title") or _title_fields(parsed)[0])
    timestamp = now()
    for key in keys:
        execute(
            "INSERT INTO match_aliases(alias_key, alias_text, media_type, tmdb_id, title, year, "
            "match, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(alias_key) DO UPDATE SET alias_text=excluded.alias_text, "
            "media_type=excluded.media_type, tmdb_id=excluded.tmdb_id, title=excluded.title, "
            "year=excluded.year, match=excluded.match, updated_at=excluded.updated_at",
            (
                key,
                _title_fields(parsed)[0],
                str(match["media_type"]),
                int(match["tmdb_id"]),
                display_title,
                year,
                dumps(payload),
                timestamp,
                timestamp,
            ),
        )


def match_from_alias(parsed: ParsedName | dict[str, Any]) -> dict[str, Any] | None:
    """按手动匹配学到的标题直接返回匹配结果；季集号仍以当前文件解析为准。"""
    keys = alias_keys(parsed)
    if not keys:
        return None
    placeholders = ",".join("?" for _ in keys)
    row = query_one(
        f"SELECT * FROM match_aliases WHERE alias_key IN ({placeholders}) LIMIT 1",
        tuple(keys),
    )
    if not row:
        return None
    media_type = str(row.get("media_type") or "")
    season = _parsed_value(parsed, "season")
    episode = _parsed_value(parsed, "episode")
    if media_type == "tv" and episode is None:
        return None
    if media_type == "movie" and (season is not None or episode is not None):
        return None
    best = loads(row.get("match"), {}) or {}
    if not best.get("tmdb_id"):
        best = {
            "tmdb_id": int(row["tmdb_id"]),
            "media_type": media_type,
            "title": row.get("title") or "",
            "year": row.get("year"),
        }
    best.setdefault("tmdb_id", int(row["tmdb_id"]))
    best.setdefault("media_type", media_type)
    return {
        "candidates": [best],
        "best": best,
        "status": "matched",
        "source": "alias",
        "confidence": 1.0,
    }


def forget_alias(parsed: ParsedName | dict[str, Any]) -> None:
    """「重新自动匹配」时一并忘掉该标题的手动学习结果。"""
    keys = alias_keys(parsed)
    if not keys:
        return
    placeholders = ",".join("?" for _ in keys)
    execute(f"DELETE FROM match_aliases WHERE alias_key IN ({placeholders})", tuple(keys))


def score_candidate(
    parsed: ParsedName,
    query: str,
    candidate: dict[str, Any],
    *,
    fallback_title: str = "",
) -> float:
    titles = [candidate.get("title") or "", candidate.get("original_title") or ""]
    best = max([title_similarity(query, title) for title in titles] + [0.0])
    if fallback_title:
        best = max(best, title_similarity(fallback_title, candidate.get("title") or "") * 0.9)
    score = best
    if parsed.year and candidate.get("year"):
        diff = abs(int(parsed.year) - int(candidate["year"]))
        if diff == 0:
            score += 0.12
        elif diff == 1:
            score += 0.04
        else:
            score -= 0.12
    if parsed.media_type and candidate.get("media_type") == parsed.media_type:
        score += 0.05
    elif parsed.media_type and candidate.get("media_type") != parsed.media_type:
        score -= 0.08
    score += min(float(candidate.get("popularity") or 0) / 4000.0, 0.05)
    return max(0.0, min(1.0, score))


def _queries(parsed: ParsedName, folder: ParsedName | None) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()
    ordered = [
        (parsed.title, parsed.title_alt),
        (folder.title if folder else "", folder.title_alt if folder else ""),
    ]
    for primary, alt in ordered:
        for text in (primary, alt):
            text = (text or "").strip()
            key = normalize_title(text)
            if len(key) < 2 or key in seen:
                continue
            seen.add(key)
            queries.append((text, parsed.media_type or (folder.media_type if folder else "")))
    return queries


# 同一目录（同一部剧）通常只需要 AI 识别一次；后续集直接复用，避免每集
# 都调用一次模型。只做进程内缓存，不把 AI 结果写入数据库。
_AI_HINT_CACHE: dict[str, dict[str, Any]] = {}


def _ai_context_key(parsed: ParsedName, folder: ParsedName | None, folder_path: str) -> str:
    context = normalize_title(folder_path or (folder.raw if folder else ""))
    if context:
        return "dir:" + context
    return "name:" + normalize_title(parsed.raw)


def _ai_hint_for(
    parsed: ParsedName,
    folder: ParsedName | None,
    folder_path: str,
) -> dict[str, Any] | None:
    key = _ai_context_key(parsed, folder, folder_path)
    cached = _AI_HINT_CACHE.get(key)
    if cached:
        return cached
    hint = parse_with_ai(parsed.raw, folder_path or (folder.raw if folder else ""))
    if hint:
        _AI_HINT_CACHE[key] = hint
    return hint


def _needs_ai(
    parsed: ParsedName,
    candidates: list[dict[str, Any]],
    review_threshold: float,
) -> bool:
    """只有在解析/候选不确定时才花钱调用 AI。"""
    if not parsed.title or len(parsed.title.strip()) < 2 or parsed.confidence < 0.6:
        return True
    if not candidates:
        return True
    top = float(candidates[0].get("score") or 0)
    if top < review_threshold:
        return True
    if len(candidates) >= 2:
        second = float(candidates[1].get("score") or 0)
        if top - second < 0.08:
            return True
    return False


def _apply_ai_boost(candidates: list[dict[str, Any]], hint: dict[str, Any]) -> None:
    titles = [
        str(hint.get(key) or "").strip()
        for key in ("title", "title_alt")
    ]
    titles.extend(str(value or "").strip() for value in (hint.get("aliases") or []))
    titles = [value for value in titles if value]
    if not titles:
        return
    for candidate in candidates:
        best = 0.0
        for candidate_title in (
            candidate.get("title") or "",
            candidate.get("original_title") or "",
        ):
            if not candidate_title:
                continue
            for title in titles:
                best = max(best, title_similarity(title, candidate_title))
        if best >= 0.8:
            candidate["score"] = round(min(1.0, float(candidate.get("score") or 0) + 0.08), 4)
            candidate["ai_boost"] = round(best, 4)


def match_item(
    parsed: ParsedName,
    folder: ParsedName | None,
    client: TMDBClient,
    *,
    auto_threshold: float = 0.72,
    review_threshold: float = 0.5,
    folder_path: str = "",
) -> dict[str, Any]:
    """Returns {"candidates": [...], "best": {...}|None, "status": str, "source": str}."""
    result: dict[str, Any] = {"candidates": [], "best": None, "status": "unmatched", "source": "heuristic"}

    if parsed.tmdb_id:
        try:
            details = client.details(int(parsed.tmdb_id), parsed.media_type or "movie")
            details["score"] = 1.0
            result["candidates"] = [details]
            result["best"] = details
            result["status"] = "matched"
            result["source"] = "tmdb_id"
            return result
        except TMDBError:
            pass

    if not client.configured:
        result["status"] = "unmatched"
        result["error"] = "未配置 TMDb API Key"
        return result

    media_type = parsed.media_type or (folder.media_type if folder else "") or ""
    search_type = media_type if media_type in {"movie", "tv"} else ""
    collected: dict[tuple[int, str], dict[str, Any]] = {}
    errors: list[str] = []

    def run_search(query: str, query_type: str, fallback: str = "") -> None:
        try:
            for candidate in client.search(query, query_type, parsed.year):
                key = (int(candidate["tmdb_id"]), candidate["media_type"])
                score = score_candidate(parsed, query, candidate, fallback_title=fallback)
                existing = collected.get(key)
                if existing is None or score > existing["score"]:
                    collected[key] = {**candidate, "score": round(score, 4), "query": query}
        except TMDBError as exc:
            errors.append(str(exc))

    for query, query_type in _queries(parsed, folder):
        run_search(query, query_type or search_type)
        if not media_type:
            run_search(query, "movie")
            run_search(query, "tv")

    # AI 辅助：解析不确定、没有候选、低分候选或候选接近时，让模型从
    # 文件名 + 上级目录给出标题、别名和季集，再回到 TMDb 验证。
    ai_hint: dict[str, Any] | None = None
    initial = sorted(collected.values(), key=lambda item: item["score"], reverse=True)
    if _needs_ai(parsed, initial, review_threshold) and ai_available():
        try:
            ai_hint = _ai_hint_for(parsed, folder, folder_path)
            if ai_hint and ai_hint.get("title"):
                query_titles: list[str] = []
                for value in (
                    ai_hint.get("title"),
                    ai_hint.get("title_alt"),
                    *(ai_hint.get("aliases") or []),
                ):
                    text = str(value or "").strip()
                    if text and text not in query_titles:
                        query_titles.append(text)
                for query in query_titles:
                    run_search(
                        query,
                        ai_hint.get("media_type") or search_type,
                        fallback=ai_hint.get("title_alt") or "",
                    )
                if ai_hint.get("year") and not parsed.year:
                    parsed.year = ai_hint["year"]
                if ai_hint.get("season") is not None and parsed.season is None:
                    parsed.season = ai_hint["season"]
                if ai_hint.get("episode") is not None and parsed.episode is None:
                    parsed.episode = ai_hint["episode"]
                if not parsed.media_type and ai_hint.get("media_type"):
                    parsed.media_type = str(ai_hint["media_type"])
                result["source"] = "ai"
                result["ai"] = {
                    key: ai_hint.get(key)
                    for key in ("title", "title_alt", "aliases", "confidence", "reason", "season", "episode")
                }
        except AIError as exc:
            errors.append(str(exc))

    candidates = sorted(collected.values(), key=lambda item: item["score"], reverse=True)
    if ai_hint:
        _apply_ai_boost(candidates, ai_hint)
        candidates.sort(key=lambda item: item["score"], reverse=True)
    candidates = candidates[:8]
    if candidates:
        top = candidates[0]["score"]
        for candidate in candidates:
            candidate["ratio_to_top1"] = round(candidate["score"] / top, 4) if top else 0.0
    result["candidates"] = candidates
    if errors and not candidates:
        result["error"] = "; ".join(errors[:2])

    if candidates:
        best = candidates[0]
        confidence = best["score"] * (0.7 + 0.3 * max(0.1, parsed.confidence))
        result["confidence"] = round(confidence, 4)
        if confidence >= auto_threshold:
            result["status"] = "matched"
        elif confidence >= review_threshold:
            result["status"] = "review"
        else:
            result["status"] = "unmatched"
        if result["status"] != "unmatched":
            result["best"] = best
    elif ai_hint and ai_hint.get("title"):
        # TMDb 仍找不到时保留 AI 建议，至少让日志 / 待确认知道模型判断；
        # 不直接据此整理，避免把模型猜测当成确认结果。
        result["status"] = "review"
        result["error"] = f"AI 建议：{ai_hint.get('title')}（TMDb 未确认）"
    return result


def validate_candidate(client: TMDBClient, tmdb_id: int, media_type: str) -> dict[str, Any]:
    return client.details(tmdb_id, media_type)


def candidates_from_search_page(
    client: TMDBClient,
    query: str,
    media_type: str = "",
    year: int | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """搜索候选并保留分页信息（供手动搜索翻页）。"""
    if hasattr(client, "search_page"):
        data = client.search_page(query, media_type, year, page)
    else:  # 兼容只实现 search() 的测试替身
        data = {
            "results": client.search(query, media_type, year),
            "page": page,
            "total_pages": 1,
            "total_results": 0,
        }
    results: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for item in data.get("results", []):
        key = (int(item["tmdb_id"]), item["media_type"])
        if key in seen:
            continue
        seen.add(key)
        results.append(item)
    return {
        "results": results,
        "page": int(data.get("page") or page),
        "total_pages": int(data.get("total_pages") or 0),
        "total_results": int(data.get("total_results") or 0),
    }


def candidates_from_search(client: TMDBClient, query: str, media_type: str = "", year: int | None = None) -> list[dict[str, Any]]:
    return candidates_from_search_page(client, query, media_type, year)["results"]


def fallback_parse_folder(path: str) -> ParsedName:
    return parse_media_name(path.rsplit("/", 1)[-1] if path else "", is_dir=True)
