from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from ...config import settings
from ...db import dumps, get_setting, loads, now, query_one, execute

API_BASE = settings.tmdb_api_base
IMAGE_BASE = settings.tmdb_image_base
CACHE_TTL_SECONDS = 7 * 86400


class TMDBError(RuntimeError):
    pass


class RateLimiter:
    def __init__(self, qps: float = 4.0):
        self.min_interval = 1.0 / max(qps, 0.2)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


def normalize_candidate(raw: dict[str, Any]) -> dict[str, Any]:
    media_type = raw.get("media_type") or ("tv" if raw.get("name") else "movie")
    title = raw.get("title") or raw.get("name") or ""
    original = raw.get("original_title") or raw.get("original_name") or ""
    date = raw.get("release_date") or raw.get("first_air_date") or ""
    year = int(date[:4]) if date[:4].isdigit() else None
    return {
        "tmdb_id": raw.get("id"),
        "media_type": media_type,
        "title": title,
        "original_title": original,
        "year": year,
        "overview": raw.get("overview") or "",
        "poster_path": raw.get("poster_path") or "",
        "backdrop_path": raw.get("backdrop_path") or "",
        "popularity": float(raw.get("popularity") or 0),
        "vote_average": float(raw.get("vote_average") or 0),
        "vote_count": int(raw.get("vote_count") or 0),
        "original_language": str(raw.get("original_language") or "").strip().lower(),
        "genre_ids": [int(value) for value in (raw.get("genre_ids") or []) if str(value).isdigit()],
        "origin_country": [str(value).upper() for value in (raw.get("origin_country") or []) if value],
    }


class TMDBClient:
    def __init__(self, api_key: str = "", language: str = "zh-CN", qps: float = 4.0):
        self.api_key = (api_key or "").strip()
        self.language = language or "zh-CN"
        self.limiter = RateLimiter(qps)
        self.client = httpx.Client(
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": "CineFlow/0.1"},
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        if self.api_key.startswith("ey"):
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def _get(self, path: str, params: dict[str, Any] | None = None, use_cache: bool = True) -> dict[str, Any]:
        params = dict(params or {})
        params.setdefault("language", self.language)
        if not self.api_key.startswith("ey"):
            params["api_key"] = self.api_key
        cache_key = path + "?" + urlencode(sorted((k, str(v)) for k, v in params.items()))
        if use_cache:
            cached = query_one("SELECT value, created_at FROM tmdb_cache WHERE key=?", (cache_key,))
            if cached:
                age = time.time() - time.mktime(time.strptime(cached["created_at"][:19], "%Y-%m-%dT%H:%M:%S"))
                if age < CACHE_TTL_SECONDS:
                    return loads(cached["value"], {})
        self.limiter.wait()
        try:
            response = self.client.get(API_BASE + path, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise TMDBError(f"TMDb 请求失败: {exc}") from exc
        if response.status_code == 401:
            raise TMDBError("TMDb API Key 无效")
        if response.status_code == 429:
            time.sleep(2)
            return self._get(path, params, use_cache=False)
        if response.status_code >= 400:
            raise TMDBError(f"TMDb 返回 HTTP {response.status_code}")
        data = response.json()
        if use_cache:
            execute(
                "INSERT INTO tmdb_cache(key, value, created_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, created_at=excluded.created_at",
                (cache_key, dumps(data), now()),
            )
        return data

    def search_page(
        self,
        query: str,
        media_type: str = "",
        year: int | None = None,
        page: int = 1,
    ) -> dict[str, Any]:
        """搜索 TMDb 并返回分页信息：results / page / total_pages / total_results。"""
        query = (query or "").strip()
        try:
            page = max(1, int(page or 1))
        except (TypeError, ValueError):
            page = 1
        if not query:
            return {"results": [], "page": page, "total_pages": 0, "total_results": 0}
        # 纠错 / 手动搜索需要能搜到 TMDb 的成人条目；TMDb 默认 include_adult=false，
        # 这里三种搜索都显式打开，否则成人作品永远不出现在候选里。
        if media_type == "movie":
            params: dict[str, Any] = {"query": query, "include_adult": "true", "page": page}
            if year:
                params["year"] = year
            data = self._get("/search/movie", params)
            results = [
                normalize_candidate({**item, "media_type": "movie"})
                for item in data.get("results", [])
            ]
        elif media_type == "tv":
            params = {"query": query, "include_adult": "true", "page": page}
            if year:
                params["first_air_date_year"] = year
            data = self._get("/search/tv", params)
            results = [
                normalize_candidate({**item, "media_type": "tv"})
                for item in data.get("results", [])
            ]
        else:
            params = {"query": query, "include_adult": "true", "page": page}
            data = self._get("/search/multi", params)
            results = [
                normalize_candidate(item)
                for item in data.get("results", [])
                if item.get("media_type") in {"movie", "tv"}
            ]
        return {
            "results": results,
            "page": int(data.get("page") or page),
            "total_pages": int(data.get("total_pages") or 0),
            "total_results": int(data.get("total_results") or 0),
        }

    def search(
        self,
        query: str,
        media_type: str = "",
        year: int | None = None,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        return self.search_page(query, media_type, year, page)["results"]

    def details(self, tmdb_id: int, media_type: str) -> dict[str, Any]:
        if media_type == "tv":
            data = self._get(
                f"/tv/{tmdb_id}",
                {"append_to_response": "images,external_ids,credits", "include_image_language": f"{self.language[:2]},en,null"},
            )
            candidate = normalize_candidate({**data, "media_type": "tv"})
            candidate["seasons"] = [
                {
                    "season_number": season.get("season_number"),
                    "name": season.get("name"),
                    "episode_count": season.get("episode_count"),
                    "poster_path": season.get("poster_path"),
                    "air_date": season.get("air_date"),
                }
                for season in data.get("seasons", [])
            ]
            candidate["number_of_seasons"] = data.get("number_of_seasons")
            candidate["number_of_episodes"] = data.get("number_of_episodes")
            candidate["status"] = data.get("status")
            candidate["genres"] = [genre.get("name") for genre in data.get("genres", [])]
            candidate["networks"] = [network.get("name") for network in data.get("networks", [])]
            candidate["logo_path"] = self._logo_path(tmdb_id, "tv", data)
            candidate["cast"] = _pick_cast(data)
            candidate["runtime"] = (data.get("episode_run_time") or [None])[0]
        else:
            data = self._get(
                f"/movie/{tmdb_id}",
                {"append_to_response": "images,external_ids,credits,release_dates", "include_image_language": f"{self.language[:2]},en,null"},
            )
            candidate = normalize_candidate({**data, "media_type": "movie"})
            candidate["genres"] = [genre.get("name") for genre in data.get("genres", [])]
            candidate["logo_path"] = self._logo_path(tmdb_id, "movie", data)
            candidate["cast"] = _pick_cast(data)
            candidate["runtime"] = data.get("runtime")
            candidate["tagline"] = data.get("tagline") or ""
            candidate["status"] = data.get("status")
            candidate["production_countries"] = [
                str(country.get("iso_3166_1") or "").upper()
                for country in data.get("production_countries") or []
                if isinstance(country, dict) and country.get("iso_3166_1")
            ]
        candidate["overview"] = candidate.get("overview") or ""
        return candidate

    def _logo_path(self, tmdb_id: int, media_type: str, data: dict[str, Any]) -> str:
        original = str(data.get("original_language") or "").lower()
        requested = {self.language[:2].lower() or "zh", "en"}
        if original and original not in requested:
            logos = (data.get("images") or {}).get("logos") or []
            if not any(str(item.get("iso_639_1") or "").lower() == original for item in logos):
                try:
                    extra = self._get(
                        f"/{media_type}/{tmdb_id}/images",
                        {"include_image_language": f"{original},en,null"},
                    )
                except TMDBError:
                    extra = {}
                extra_logos = extra.get("logos") or []
                if extra_logos:
                    data.setdefault("images", {})["logos"] = [*logos, *extra_logos]
        return _pick_logo(data)

    def season(self, tv_id: int, season_number: int) -> dict[str, Any]:
        data = self._get(
            f"/tv/{tv_id}/season/{season_number}",
            {"append_to_response": "images,credits"},
        )
        return {
            "season_number": data.get("season_number"),
            "name": data.get("name"),
            "overview": data.get("overview") or "",
            "poster_path": data.get("poster_path") or "",
            "air_date": data.get("air_date") or "",
            "episodes": [
                {
                    "episode_number": episode.get("episode_number"),
                    "name": episode.get("name") or "",
                    "overview": episode.get("overview") or "",
                    "still_path": episode.get("still_path") or "",
                    "air_date": episode.get("air_date") or "",
                    "runtime": episode.get("runtime"),
                }
                for episode in data.get("episodes", [])
            ],
        }


def _pick_logo(data: dict[str, Any]) -> str:
    images = (data.get("images") or {}).get("logos") or []
    if not images:
        return ""
    original = str(data.get("original_language") or "").lower()

    def score(item: dict[str, Any]) -> tuple[int, float]:
        iso = str(item.get("iso_639_1") or "").lower()
        if original and iso == original:
            priority = 0
        elif iso == "en":
            priority = 1
        elif not iso:
            priority = 2
        else:
            priority = 3
        return (priority, -float(item.get("vote_average") or 0))

    best = sorted(images, key=score)[0]
    if score(best)[0] > 2:
        return ""
    return best.get("file_path") or ""


def _pick_cast(data: dict[str, Any], limit: int = 10) -> list[dict[str, str]]:
    cast = ((data.get("credits") or {}).get("cast") or [])[:limit]
    return [
        {
            "name": person.get("name") or "",
            "role": person.get("character") or "",
            "profile_path": person.get("profile_path") or "",
        }
        for person in cast
    ]


def image_url(path: str, size: str = "w500") -> str:
    if not path:
        return ""
    if path.startswith(IMAGE_BASE + "/"):
        remaining = path[len(IMAGE_BASE) + 1 :]
        remote_size, _, remote_path = remaining.partition("/")
        if remote_path:
            return _proxied_image(remote_path, remote_size if remote_size in ALLOWED_IMAGE_SIZES else size)
        return path
    if path.startswith("http") or path.startswith("/api/"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return _proxied_image(path, size)


ALLOWED_IMAGE_SIZES = {
    "w45",
    "w92",
    "w154",
    "w185",
    "w200",
    "w300",
    "w342",
    "w500",
    "w780",
    "w1280",
    "original",
}


def _proxied_image(path: str, size: str) -> str:
    if size not in ALLOWED_IMAGE_SIZES:
        size = "w500"
    return f"/api/images?{urlencode({'path': path, 'size': size})}"


def client_from_settings() -> TMDBClient:
    from ...security import decrypt

    api_key = decrypt(get_setting("tmdb_api_key", "") or "")
    language = get_setting("tmdb_language", "zh-CN") or "zh-CN"
    qps = float(get_setting("tmdb_qps", 4) or 4)
    return TMDBClient(api_key=api_key, language=language, qps=qps)
