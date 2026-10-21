from __future__ import annotations

from xml.sax.saxutils import escape
from typing import Any

import httpx

from .metadata.art import poster_svg, backdrop_svg
from .metadata.tmdb import IMAGE_BASE


def _xml_text(value: Any) -> str:
    return escape(str(value or ""))


def _nfo_header() -> str:
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _unique_ids(match: dict[str, Any]) -> str:
    lines = []
    tmdb_id = match.get("tmdb_id")
    if tmdb_id:
        lines.append(f'  <uniqueid type="tmdb" default="true">{tmdb_id}</uniqueid>')
        lines.append(f"  <tmdbid>{tmdb_id}</tmdbid>")
    imdb_id = match.get("imdb_id")
    if imdb_id:
        lines.append(f'  <uniqueid type="imdb">{_xml_text(imdb_id)}</uniqueid>')
    return "\n".join(lines)


def _cast_block(match: dict[str, Any]) -> str:
    blocks = []
    for person in (match.get("cast") or [])[:10]:
        blocks.append(
            "  <actor>\n"
            f"    <name>{_xml_text(person.get('name'))}</name>\n"
            f"    <role>{_xml_text(person.get('role'))}</role>\n"
            + (
                f"    <thumb>{_xml_text('https://image.tmdb.org/t/p/w185' + person['profile_path'])}</thumb>\n"
                if person.get("profile_path")
                else ""
            )
            + "  </actor>"
        )
    return "\n".join(blocks)


def _genres_block(match: dict[str, Any]) -> str:
    return "\n".join(f"  <genre>{_xml_text(genre)}</genre>" for genre in (match.get("genres") or [])[:8])


def movie_nfo(match: dict[str, Any], *, poster: bool = True, fanart: bool = True) -> str:
    tmdb_id = match.get("tmdb_id")
    lines = [
        _nfo_header().rstrip("\n"),
        "<movie>",
        f"  <title>{_xml_text(match.get('title'))}</title>",
        f"  <originaltitle>{_xml_text(match.get('original_title'))}</originaltitle>",
        f"  <sorttitle>{_xml_text(match.get('title'))}</sorttitle>",
        f"  <year>{_xml_text(match.get('year'))}</year>",
        _unique_ids(match),
        f"  <plot>{_xml_text(match.get('overview'))}</plot>",
        f"  <tagline>{_xml_text(match.get('tagline'))}</tagline>",
        f"  <runtime>{_xml_text(match.get('runtime'))}</runtime>",
        f"  <rating>{_xml_text(match.get('vote_average'))}</rating>",
        f"  <votes>{_xml_text(match.get('vote_count'))}</votes>",
        f"  <premiered>{_xml_text(match.get('premiere_date'))}</premiered>",
        f"  <status>{_xml_text(match.get('status'))}</status>",
        _genres_block(match),
        _cast_block(match),
        "  <poster>poster.jpg</poster>" if poster else "",
        "  <fanart><thumb>fanart.jpg</thumb></fanart>" if fanart else "",
        "</movie>",
    ]
    return "\n".join(line for line in lines if line)


def tvshow_nfo(match: dict[str, Any], *, poster: bool = True, fanart: bool = True) -> str:
    lines = [
        _nfo_header().rstrip("\n"),
        "<tvshow>",
        f"  <title>{_xml_text(match.get('title'))}</title>",
        f"  <originaltitle>{_xml_text(match.get('original_title'))}</originaltitle>",
        f"  <sorttitle>{_xml_text(match.get('title'))}</sorttitle>",
        f"  <year>{_xml_text(match.get('year'))}</year>",
        _unique_ids(match),
        f"  <plot>{_xml_text(match.get('overview'))}</plot>",
        f"  <rating>{_xml_text(match.get('vote_average'))}</rating>",
        f"  <votes>{_xml_text(match.get('vote_count'))}</votes>",
        f"  <status>{_xml_text(match.get('status'))}</status>",
        f"  <premiered>{_xml_text(match.get('premiere_date'))}</premiered>",
        _genres_block(match),
        _cast_block(match),
        "  <poster>poster.jpg</poster>" if poster else "",
        "  <fanart><thumb>fanart.jpg</thumb></fanart>" if fanart else "",
        "</tvshow>",
    ]
    return "\n".join(line for line in lines if line)


def season_nfo(season_number: int, season: dict[str, Any]) -> str:
    return "\n".join(
        [
            _nfo_header().rstrip("\n"),
            "<season>",
            f"  <seasonnumber>{season_number}</seasonnumber>",
            f"  <title>{_xml_text(season.get('name') or f'Season {season_number}')}</title>",
            f"  <plot>{_xml_text(season.get('overview'))}</plot>",
            f"  <premiered>{_xml_text(season.get('air_date'))}</premiered>",
            "  <poster>poster.jpg</poster>",
            "</season>",
        ]
    )


def episode_nfo(
    match: dict[str, Any],
    season_number: int,
    episode_number: int,
    episode: dict[str, Any] | None,
) -> str:
    episode = episode or {}
    title = episode.get("name") or f"S{season_number:02d}E{episode_number:02d}"
    lines = [
        _nfo_header().rstrip("\n"),
        "<episodedetails>",
        f"  <title>{_xml_text(title)}</title>",
        f"  <season>{season_number}</season>",
        f"  <episode>{episode_number}</episode>",
        f"  <plot>{_xml_text(episode.get('overview'))}</plot>",
        f"  <aired>{_xml_text(episode.get('air_date'))}</aired>",
        f"  <runtime>{_xml_text(episode.get('runtime'))}</runtime>",
        f"  <showtitle>{_xml_text(match.get('title'))}</showtitle>",
        _unique_ids(match),
        "</episodedetails>",
    ]
    return "\n".join(line for line in lines if line)


class ImageFetcher:
    def __init__(self) -> None:
        self.client = httpx.Client(timeout=httpx.Timeout(60.0), follow_redirects=True)
        self._cache: dict[tuple[str, str], bytes] = {}

    def fetch(self, path: str, size: str = "w780", title: str = "", subtitle: str = "") -> bytes | None:
        if not path:
            return None
        key = (path, size)
        if key in self._cache:
            return self._cache[key]
        try:
            if path.startswith("/api/demo/art/poster"):
                data = poster_svg(title or "Demo", subtitle, path).encode("utf-8")
            elif path.startswith("/api/demo/art/backdrop"):
                data = backdrop_svg(title or "Demo", path).encode("utf-8")
            elif path.startswith("http"):
                response = self.client.get(path)
                data = response.content if response.status_code == 200 else None
            else:
                response = self.client.get(f"{IMAGE_BASE}/{size}{path}")
                data = response.content if response.status_code == 200 else None
        except httpx.HTTPError:
            return None
        if data:
            self._cache[key] = data
        return data


SCENE_IMAGE_EXT = "jpg"
