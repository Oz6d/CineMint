from __future__ import annotations

from typing import Any

from ..db import get_setting
from .metadata.ai import AIError, ai_available, parse_with_ai
from .metadata.tmdb import TMDBClient, TMDBError, normalize_candidate
from .parser import ParsedName, normalize_title, parse_media_name, title_similarity


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


def match_item(
    parsed: ParsedName,
    folder: ParsedName | None,
    client: TMDBClient,
    *,
    auto_threshold: float = 0.72,
    review_threshold: float = 0.5,
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

    # AI 兜底：标题太短或没有候选时，请模型读一遍文件名
    if (not collected or max((c["score"] for c in collected.values()), default=0) < review_threshold) and ai_available():
        try:
            hint = parse_with_ai(parsed.raw, "")
            if hint and hint.get("title"):
                run_search(hint["title"], hint.get("media_type") or search_type, fallback=hint.get("title_alt") or "")
                if hint.get("year") and not parsed.year:
                    parsed.year = hint["year"]
                if hint.get("season") and not parsed.season:
                    parsed.season = hint["season"]
                if hint.get("episode") and not parsed.episode:
                    parsed.episode = hint["episode"]
                result["source"] = "ai"
        except AIError as exc:
            errors.append(str(exc))

    candidates = sorted(collected.values(), key=lambda item: item["score"], reverse=True)[:8]
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
    return result


def validate_candidate(client: TMDBClient, tmdb_id: int, media_type: str) -> dict[str, Any]:
    return client.details(tmdb_id, media_type)


def candidates_from_search(client: TMDBClient, query: str, media_type: str = "", year: int | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for item in client.search(query, media_type, year):
        key = (int(item["tmdb_id"]), item["media_type"])
        if key in seen:
            continue
        seen.add(key)
        results.append(item)
    return results


def fallback_parse_folder(path: str) -> ParsedName:
    return parse_media_name(path.rsplit("/", 1)[-1] if path else "", is_dir=True)
