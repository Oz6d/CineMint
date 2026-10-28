"""Bangumi 放送日历与蜜柑追更。

Bangumi 提供每周放送表；蜜柑番剧 RSS 提供实际的资源更新。
两者都不依赖下载器，只负责维护追更记录与下一次放送时间。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from ..db import (
    dumps,
    execute,
    get_setting,
    loads,
    now,
    query_all,
    query_one,
    set_setting,
)
from .parser import parse_media_name
from .rss import RssError, fetch_feed
from .scheduling import DEFAULT_TIMEZONE

DEFAULT_API_BASE = "https://api.bgm.tv"
API_USER_AGENT = "CineMint/0.1 (+self-hosted media organizer)"
CACHE_SETTING = "bangumi_calendar_cache"
CACHE_SECONDS = 6 * 3600
WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
BANGUMI_LINK_RE = re.compile(
    r"(?:bgm\.tv|bangumi\.tv|bangumi\.cn)/subject/(\d+)", re.I
)
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
BANGUMI_HOSTS = {"mikanani.me", "mikanime.tv", "www.mikanani.me", "www.mikanime.tv"}
WEEKDAY_NAME_MAP = {
    "星期一": 0,
    "星期二": 1,
    "星期三": 2,
    "星期四": 3,
    "星期五": 4,
    "星期六": 5,
    "星期日": 6,
    "星期天": 6,
}
TERMINAL_EPISODE_STATUSES = {"organized", "duplicate"}


class BangumiError(RuntimeError):
    pass


def normalize_air_time(value: Any) -> str:
    match = TIME_RE.match(str(value or "").strip())
    if not match:
        return ""
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo("UTC")


def _api_base() -> str:
    value = str(get_setting("bangumi_api_base", DEFAULT_API_BASE) or "").strip()
    if not value.startswith(("http://", "https://")):
        return DEFAULT_API_BASE
    return value.rstrip("/")


def _get_json(url: str, timeout: float = 25.0) -> Any:
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": API_USER_AGENT,
                "Accept": "application/json",
            },
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise BangumiError(f"Bangumi 请求失败: {type(exc).__name__}") from exc
    if response.status_code == 404:
        raise BangumiError("Bangumi 条目不存在")
    if response.status_code >= 400:
        raise BangumiError(f"Bangumi HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise BangumiError("Bangumi 返回了无法解析的内容") from exc


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _weekday_index(value: Any, fallback: int = 0) -> int:
    number = _int(value)
    if number is None:
        return fallback % 7
    # Bangumi 用 1=周一 … 7=周日，内部统一为 0=周一 … 6=周日。
    return (number - 1) % 7


def _clean_title(raw: str) -> tuple[str, str]:
    title = (raw or "").strip()
    for prefix in ("Mikan Project - ", "Mikan Project -", "蜜柑计划 - ", "蜜柑计划 -"):
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
            break
    subgroup = ""
    match = re.match(r"^\[([^\]]+)\]\s*(.+)$", title)
    if match:
        subgroup, title = match.group(1).strip(), match.group(2).strip()
    return title[:300], subgroup[:200]


def _poster(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("large", "common", "medium", "small", "grid"):
            if value.get(key):
                return str(value[key])
    if isinstance(value, str):
        return value
    return ""


def _mikan_api_base() -> str:
    value = str(get_setting("mikan_api_base", "https://mikanime.tv") or "").strip()
    if not value.startswith(("http://", "https://")):
        return "https://mikanime.tv"
    return value.rstrip("/")


def _mikan_hosts() -> set[str]:
    hosts = set(BANGUMI_HOSTS)
    try:
        host = urlparse(str(get_setting("mikan_api_base", "") or "")).netloc.lower()
    except Exception:
        host = ""
    if host:
        hosts.add(host)
    return hosts


def _mikan_soup(path: str) -> BeautifulSoup:
    base = _mikan_api_base()
    try:
        with httpx.Client(
            timeout=25.0,
            follow_redirects=True,
            headers={
                "User-Agent": API_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        ) as client:
            response = client.get(f"{base}{path}")
    except httpx.HTTPError as exc:
        raise BangumiError(f"蜜柑计划请求失败：{type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise BangumiError(f"蜜柑计划 HTTP {response.status_code}")
    try:
        return BeautifulSoup(response.text, "html.parser")
    except Exception as exc:
        raise BangumiError("蜜柑计划页面解析失败") from exc


def mikan_detail(mikan_id: int) -> dict[str, Any]:
    """解析蜜柑番剧页：真实 Bangumi 条目、海报、星期与字幕组 RSS。"""
    base = _mikan_api_base()
    try:
        soup = _mikan_soup(f"/Home/Bangumi/{int(mikan_id)}")
    except BangumiError:
        raise
    left = soup.select_one("div.leftbar-container") or soup

    title = ""
    title_el = left.select_one("p.bangumi-title")
    if title_el:
        title, _ = _clean_title(title_el.get_text(" ", strip=True))

    poster = ""
    poster_el = left.select_one("div.bangumi-poster")
    if poster_el:
        style = str(poster_el.get("style") or "")
        match = re.search(r"url\(['\"]?([^'\")]+)", style)
        if match:
            poster = urljoin(base, match.group(1))

    bangumi_id = None
    weekday = None
    for info in left.select("p.bangumi-info"):
        text = info.get_text(" ", strip=True)
        day_match = re.search(r"放送日期[:：]\s*(星期[一二三四五六日天])", text)
        if day_match and weekday is None:
            weekday = WEEKDAY_NAME_MAP.get(day_match.group(1))
        for anchor in info.select("a[href]"):
            match = BANGUMI_LINK_RE.search(str(anchor.get("href") or ""))
            if match:
                bangumi_id = int(match.group(1))
                break
        if bangumi_id is not None and weekday is not None:
            break

    subgroups: list[dict[str, Any]] = []
    seen: set[int] = set()
    for div in soup.select("div.subgroup-text[id]"):
        subgroup_id = _int(div.get("id"))
        rss_el = div.find("a", class_="mikan-rss", href=True)
        if not subgroup_id or subgroup_id in seen or not rss_el:
            continue
        seen.add(subgroup_id)
        name_el = div.find("a", href=re.compile(r"/Home/PublishGroup/"))
        name = name_el.get_text(strip=True) if name_el else f"字幕组 {subgroup_id}"
        subgroups.append(
            {
                "id": subgroup_id,
                "name": name[:100],
                "feed_url": urljoin(base, str(rss_el.get("href") or "")),
            }
        )

    default_el = soup.select_one("p.bangumi-title a.mikan-rss[href]")
    if default_el:
        feed_url = urljoin(base, str(default_el.get("href") or ""))
    else:
        feed_url = f"{base}/RSS/Bangumi?bangumiId={int(mikan_id)}"

    return {
        "mikan_id": int(mikan_id),
        "bangumi_id": bangumi_id,
        "title": title[:300],
        "title_original": title[:300],
        "poster": poster[:1000],
        "weekday": weekday,
        "weekday_label": WEEKDAY_LABELS[weekday] if weekday is not None else "",
        "feed_url": feed_url[:1000],
        "subgroups": subgroups,
    }


def preview_feed(feed_url: str, limit: int = 50) -> dict[str, Any]:
    value = str(feed_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in _mikan_hosts():
        raise BangumiError("只支持预览蜜柑计划的 RSS 地址")
    try:
        result = fetch_feed(value)
    except RssError as exc:
        raise BangumiError(str(exc)) from exc
    if result.error:
        raise BangumiError(result.error)
    image = urljoin(value, result.image) if result.image else ""
    items = [
        {
            "title": item.title[:300],
            "link": item.link[:1000],
            "size": item.size,
            "published_at": item.published_at,
        }
        for item in result.items[: max(1, min(limit, 100))]
    ]
    return {
        "feed_url": value[:1000],
        "title": result.title[:300],
        "image": image[:1000],
        "total": len(result.items),
        "items": items,
        "truncated": len(result.items) > len(items),
    }


def _mikan_row_label(box: Any) -> str:
    """向上找到最近一个有文字的 row，作为该列表所属分区。"""
    sibling = box
    while True:
        sibling = sibling.find_previous_sibling()
        if sibling is None:
            return ""
        if sibling.name != "div" or "row" not in (sibling.get("class") or []):
            continue
        text = sibling.get_text(" ", strip=True)
        if text:
            return text


def _fetch_mikan_calendar() -> list[dict[str, Any]]:
    """从蜜柑计划抓取本周放送日历。"""
    base = _mikan_api_base()
    soup = _mikan_soup("/")

    # 每个星期分成多个 an-box（每行约 5 部），同一天需要合并
    grouped: dict[int, list[dict[str, Any]]] = {}
    for box in soup.find_all("div", class_="an-box"):
        weekday_text = _mikan_row_label(box)
        internal_weekday = WEEKDAY_NAME_MAP.get(weekday_text)
        if internal_weekday is None:
            continue
        items = grouped.setdefault(internal_weekday, [])
        for li in box.find_all("li"):
            poster_span = li.find("span", class_="js-expand_bangumi")
            poster = ""
            mikan_id = None
            if poster_span:
                poster = poster_span.get("data-src", "")
                if poster and not poster.startswith(("http://", "https://")):
                    poster = urljoin(base, poster)
                mikan_id = poster_span.get("data-bangumiid")
                if mikan_id:
                    try:
                        mikan_id = int(mikan_id)
                    except (TypeError, ValueError):
                        mikan_id = None

            title_link = li.find("a", class_="an-text")
            if not title_link:
                continue
            title = title_link.get_text(strip=True)
            if not title:
                continue
            link = title_link.get("href", "")
            if link and not link.startswith(("http://", "https://")):
                link = urljoin(base, link)
            title, subgroup = _clean_title(title)
            items.append({
                "bangumi_id": None,
                "mikan_id": mikan_id,
                "title": title[:300],
                "title_original": title[:300],
                "poster": poster[:1000],
                "air_date": "",
                "weekday": internal_weekday,
                "rating": 0,
                "summary": "",
                "url": link[:1000],
                "subgroup": subgroup,
            })

    days = [
        {
            "weekday": weekday,
            "label": WEEKDAY_LABELS[weekday],
            "items": items,
        }
        for weekday, items in sorted(grouped.items())
        if items
    ]
    if not days:
        raise BangumiError("蜜柑计划日历为空，请检查网络或稍后重试")
    return days


def _normalize_calendar_item(raw: dict[str, Any], weekday: int) -> dict[str, Any]:
    title_original = str(raw.get("name") or "").strip()
    title = str(raw.get("name_cn") or "").strip() or title_original
    rating = raw.get("rating") if isinstance(raw.get("rating"), dict) else {}
    return {
        "bangumi_id": _int(raw.get("id")),
        "title": title[:300],
        "title_original": title_original[:300],
        "poster": _poster(raw.get("images"))[:1000],
        "air_date": str(raw.get("air_date") or "")[:40],
        "weekday": _weekday_index(raw.get("air_weekday"), weekday),
        "rating": float(rating.get("score") or 0),
        "summary": str(raw.get("summary") or "")[:500],
        "url": f"https://bgm.tv/subject/{_int(raw.get('id'))}"
        if _int(raw.get("id"))
        else "",
    }


def fetch_calendar() -> list[dict[str, Any]]:
    data = _get_json(f"{_api_base()}/calendar")
    if not isinstance(data, list):
        raise BangumiError("Bangumi 放送日历格式异常")
    days: list[dict[str, Any]] = []
    for index, group in enumerate(data[:7]):
        if not isinstance(group, dict):
            continue
        weekday_raw = group.get("weekday") if isinstance(group.get("weekday"), dict) else {}
        weekday = _weekday_index(weekday_raw.get("id"), index)
        items = [
            _normalize_calendar_item(item, weekday)
            for item in (group.get("items") or [])
            if isinstance(item, dict)
        ]
        items.sort(key=lambda item: (-item["rating"], item["title"]))
        days.append(
            {
                "weekday": weekday,
                "label": WEEKDAY_LABELS[weekday],
                "items": [item for item in items if item["bangumi_id"]],
            }
        )
    if not days:
        raise BangumiError("Bangumi 放送日历为空")
    return sorted(days, key=lambda day: day["weekday"])


def calendar(force: bool = False) -> dict[str, Any]:
    cached = loads(get_setting(CACHE_SETTING, ""), {}) or {}
    fetched_at = str(cached.get("fetched_at") or "")
    if not force and fetched_at and cached.get("days"):
        try:
            age = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            ).total_seconds()
        except ValueError:
            age = CACHE_SECONDS + 1
        if age < CACHE_SECONDS:
            return {
                "days": cached["days"],
                "fetched_at": fetched_at,
                "stale": False,
                "error": "",
            }
    
    bangumi_enabled = bool(get_setting("bangumi_enabled", False))
    all_days: list[dict[str, Any]] = []
    errors: list[str] = []
    
    # 优先从蜜柑计划获取日历
    try:
        mikan_days = _fetch_mikan_calendar()
        all_days.extend(mikan_days)
    except BangumiError as exc:
        errors.append(f"蜜柑计划：{exc}")
    
    # 如果启用了 Bangumi，合并 Bangumi 数据
    if bangumi_enabled:
        try:
            bangumi_days = fetch_calendar()
            # 合并数据：按 weekday 合并，去重（基于 title）
            mikan_titles_by_weekday = {}
            for day in all_days:
                weekday = day["weekday"]
                if weekday not in mikan_titles_by_weekday:
                    mikan_titles_by_weekday[weekday] = set()
                for item in day["items"]:
                    mikan_titles_by_weekday[weekday].add(item["title"])
            
            for day in bangumi_days:
                weekday = day["weekday"]
                existing_titles = mikan_titles_by_weekday.get(weekday, set())
                new_items = [
                    item for item in day["items"]
                    if item["title"] not in existing_titles
                ]
                if new_items:
                    # 查找或创建对应的 day
                    target_day = next((d for d in all_days if d["weekday"] == weekday), None)
                    if target_day:
                        target_day["items"].extend(new_items)
                        target_day["items"].sort(key=lambda x: (-x.get("rating", 0), x["title"]))
                    else:
                        all_days.append({
                            "weekday": weekday,
                            "label": day["label"],
                            "items": new_items,
                        })
        except BangumiError as exc:
            errors.append(f"Bangumi: {exc}")
    
    if not all_days:
        if cached.get("days"):
            return {
                "days": cached["days"],
                "fetched_at": fetched_at,
                "stale": True,
                "error": "；".join(errors) if errors else "日历数据为空",
            }
        raise BangumiError("；".join(errors) if errors else "日历数据为空")
    
    all_days.sort(key=lambda day: day["weekday"])
    fetched_at = now()
    set_setting(CACHE_SETTING, dumps({"fetched_at": fetched_at, "days": all_days}))
    return {
        "days": all_days,
        "fetched_at": fetched_at,
        "stale": False,
        "error": "；".join(errors) if errors else "",
    }


def subject(bangumi_id: int, timeout: float = 10.0) -> dict[str, Any]:
    data = _get_json(f"{_api_base()}/v0/subjects/{int(bangumi_id)}", timeout=timeout)
    if not isinstance(data, dict):
        raise BangumiError("Bangumi 条目格式异常")
    title_original = str(data.get("name") or "").strip()
    title = str(data.get("name_cn") or "").strip() or title_original
    rating = data.get("rating") if isinstance(data.get("rating"), dict) else {}
    eps = _int(data.get("eps") or data.get("total_episodes"))
    return {
        "bangumi_id": _int(data.get("id")) or int(bangumi_id),
        "title": title[:300],
        "title_original": title_original[:300],
        "poster": _poster(data.get("images"))[:1000],
        "air_date": str(data.get("date") or "")[:40],
        "total_episodes": eps,
        "rating": float(rating.get("score") or 0),
        "summary": str(data.get("summary") or "")[:500],
    }


def _parse_mikan_ids(feed_url: str) -> tuple[int | None, int | None]:
    parsed = urlparse(feed_url)
    netloc = parsed.netloc.lower()
    if netloc not in _mikan_hosts():
        return None, None
    query = parse_qs(parsed.query)
    bangumi_id = _int((query.get("bangumiId") or [None])[0])
    subgroup_id = _int((query.get("subgroupid") or [None])[0])
    return bangumi_id, subgroup_id


def _feed_episode(item: Any) -> float | None:
    parsed = parse_media_name(str(item.title or ""))
    return float(parsed.episode) if parsed.episode is not None else None


def _published(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _schedule_from_time(value: str) -> tuple[int | None, str]:
    parsed = _published(value)
    if parsed is None:
        return None, ""
    local = parsed.astimezone(_zone())
    return local.weekday(), local.strftime("%H:%M")


def next_air_at(
    weekday: int | None,
    air_time: str,
    *,
    after: datetime | None = None,
) -> str | None:
    if weekday is None:
        return None
    match = TIME_RE.match(str(air_time or "").strip())
    hour, minute = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    zone = _zone()
    base = (after or datetime.now(timezone.utc)).astimezone(zone)
    for offset in range(8):
        day = base.date() + timedelta(days=offset)
        if day.weekday() != int(weekday) % 7:
            continue
        candidate = datetime(
            day.year, day.month, day.day, hour, minute, tzinfo=zone
        )
        if candidate > base:
            return candidate.astimezone(timezone.utc).isoformat(timespec="seconds")
    return None


def parse_follow_feed(feed_url: str) -> dict[str, Any]:
    value = str(feed_url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BangumiError("请填写有效的 RSS 地址")
    try:
        result = fetch_feed(value)
    except RssError as exc:
        raise BangumiError(str(exc)) from exc
    if result.error:
        raise BangumiError(result.error)
    if not result.items:
        raise BangumiError("RSS 中没有条目，无法建立追更记录")
    title, subgroup = _clean_title(result.title)
    bangumi_id, subgroup_id = _parse_mikan_ids(value)
    if bangumi_id is None:
        for item in result.items:
            haystack = " ".join(
                [
                    str(item.link or ""),
                    str(item.summary or ""),
                    str(item.raw.get("link") or ""),
                ]
            )
            match = BANGUMI_LINK_RE.search(haystack)
            if match:
                bangumi_id = int(match.group(1))
                break
    if not subgroup:
        for item in result.items:
            match = re.match(r"^\[([^\]]+)\]", str(item.title or ""))
            if match:
                subgroup = match.group(1).strip()[:200]
                break
    latest_episode: float | None = None
    latest_at = ""
    newest_at = ""
    for item in result.items:
        episode = _feed_episode(item)
        if episode is not None and (
            latest_episode is None or episode > latest_episode
        ):
            latest_episode = episode
            latest_at = str(item.published_at or "")
        if str(item.published_at or "") > newest_at:
            newest_at = str(item.published_at or "")
    schedule_source = latest_at or newest_at
    weekday, air_time = _schedule_from_time(schedule_source)
    poster = result.image
    if poster:
        poster = urljoin(value, poster)
    inferred_next = next_air_at(weekday, air_time)
    if inferred_next is None and schedule_source:
        moment = _published(schedule_source)
        if moment is not None:
            inferred_next = (moment + timedelta(days=7)).astimezone(
                timezone.utc
            ).isoformat(timespec="seconds")
    return {
        "feed_url": value,
        "title": (title or value)[:300],
        "subgroup_name": subgroup,
        "mikan_bangumi_id": bangumi_id,
        "subgroup_id": subgroup_id,
        "latest_episode": latest_episode,
        "next_episode": latest_episode + 1 if latest_episode is not None else None,
        "weekday": weekday,
        "air_time": air_time,
        "next_air_at": inferred_next,
        "poster": poster[:1000],
        "latest_at": schedule_source,
    }


def _current_latest(subscription_id: int) -> float | None:
    placeholders = ",".join("?" for _ in TERMINAL_EPISODE_STATUSES)
    row = query_one(
        "SELECT MAX(episode) AS episode FROM subscription_entries "
        f"WHERE subscription_id=? AND episode IS NOT NULL AND status IN ({placeholders})",
        (subscription_id, *TERMINAL_EPISODE_STATUSES),
    )
    if not row or row.get("episode") is None:
        return None
    try:
        return float(row["episode"])
    except (TypeError, ValueError):
        return None


def refresh_follow(follow: dict[str, Any]) -> dict[str, Any]:
    feed_url = str(follow.get("feed_url") or "").strip()
    if not feed_url:
        return follow
    try:
        parsed = parse_follow_feed(feed_url)
    except BangumiError as exc:
        execute(
            "UPDATE bangumi_follows SET last_error=?, updated_at=? WHERE id=?",
            (str(exc)[:500], now(), follow["id"]),
        )
        follow["last_error"] = str(exc)[:500]
        return follow
    current = follow.get("latest_episode")
    try:
        current_value = float(current) if current is not None else None
    except (TypeError, ValueError):
        current_value = None
    new_latest = parsed.get("latest_episode")
    latest = max(
        [value for value in (current_value, new_latest) if value is not None],
        default=None,
    )
    weekday = follow.get("weekday")
    if weekday is None:
        weekday = parsed.get("weekday")
    air_time = str(follow.get("air_time") or "") or parsed.get("air_time") or ""
    total = follow.get("total_episodes")
    next_episode = latest + 1 if latest is not None else None
    status = str(follow.get("status") or "following")
    next_at = next_air_at(_int(weekday), air_time)
    if total is not None and latest is not None and latest >= int(total):
        next_episode = None
        next_at = None
        status = "completed"
    elif status == "completed":
        status = "following"
    poster = str(follow.get("poster") or "") or str(parsed.get("poster") or "")
    subgroup = str(follow.get("subgroup_name") or "") or str(
        parsed.get("subgroup_name") or ""
    )
    execute(
        "UPDATE bangumi_follows SET poster=?, mikan_bangumi_id=COALESCE(?, mikan_bangumi_id), "
        "subgroup_id=COALESCE(?, subgroup_id), subgroup_name=?, weekday=?, air_time=?, "
        "latest_episode=?, next_episode=?, next_air_at=?, status=?, last_error='', "
        "last_synced_at=?, updated_at=? WHERE id=?",
        (
            poster,
            parsed.get("mikan_bangumi_id"),
            parsed.get("subgroup_id"),
            subgroup,
            _int(weekday),
            air_time,
            latest,
            next_episode,
            next_at,
            status,
            now(),
            now(),
            follow["id"],
        ),
    )
    return query_one("SELECT * FROM bangumi_follows WHERE id=?", (follow["id"],)) or follow


def refresh_follows() -> dict[str, int]:
    rows = query_all(
        "SELECT * FROM bangumi_follows WHERE status IN ('following','paused') "
        "AND feed_url<>'' ORDER BY id"
    )
    result = {"checked": 0, "updated": 0, "failed": 0}
    for row in rows:
        result["checked"] += 1
        refreshed = refresh_follow(row)
        if refreshed.get("last_error"):
            result["failed"] += 1
        else:
            result["updated"] += 1
    return result


def sync_follows_for_subscription(subscription_id: int) -> None:
    rows = query_all(
        "SELECT * FROM bangumi_follows WHERE subscription_id=? AND status='following'",
        (subscription_id,),
    )
    if not rows:
        return
    latest = _current_latest(subscription_id)
    if latest is None:
        return
    for follow in rows:
        current = follow.get("latest_episode")
        try:
            current_value = float(current) if current is not None else None
        except (TypeError, ValueError):
            current_value = None
        if current_value is not None and current_value >= latest:
            continue
        total = follow.get("total_episodes")
        next_episode = latest + 1
        status = "following"
        next_at = next_air_at(_int(follow.get("weekday")), str(follow.get("air_time") or ""))
        if total is not None and latest >= int(total):
            next_episode = None
            next_at = None
            status = "completed"
        execute(
            "UPDATE bangumi_follows SET latest_episode=?, next_episode=?, next_air_at=?, "
            "status=?, last_synced_at=?, updated_at=? WHERE id=?",
            (latest, next_episode, next_at, status, now(), now(), follow["id"]),
        )


def ensure_subscription_feed(subscription_id: int, feed_url: str) -> None:
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    if not row or row.get("type") != "rss" or not feed_url:
        return
    config = loads(row.get("config"), {}) or {}
    raw_feeds = config.get("feed_urls")
    if isinstance(raw_feeds, str):
        feeds = [raw_feeds]
    else:
        feeds = [str(value) for value in (raw_feeds or []) if value]
    if feed_url in feeds:
        return
    feeds.append(feed_url)
    config["feed_urls"] = feeds[:20]
    execute(
        "UPDATE subscriptions SET config=?, updated_at=? WHERE id=?",
        (dumps(config), now(), subscription_id),
    )
