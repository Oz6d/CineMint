from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import bangumi as tracker
from ..core.cloud_paths import map_cloud_folder
from ..core.rss import RssError, normalize_config as normalize_rss_config
from ..core.scheduling import (
    DEFAULT_TIMEZONE,
    CronError,
    CronExpression,
    next_run_for_subscription,
    normalize_schedule,
)
from ..db import db, dumps, execute, loads, now, query_all, query_one
from .deps import require_auth

router = APIRouter(
    prefix="/api/bangumi", tags=["bangumi"], dependencies=[Depends(require_auth)]
)

FOLLOW_STATUSES = {"following", "paused", "completed"}


class FollowCreate(BaseModel):
    feed_url: str = ""
    bangumi_id: int | None = None
    title: str = ""
    title_original: str = ""
    poster: str = ""
    weekday: int | None = None
    total_episodes: int | None = None
    subgroup_name: str = ""
    mikan_bangumi_id: int | None = None
    subgroup_id: int | None = None
    library_id: int = 0
    save_subdir: str = ""
    save_root: bool | None = None
    p115_folder_id: str = ""
    p115_folder_path: str = ""
    keyword: str = ""
    regex: str = ""
    min_size_mb: float = 0
    max_per_run: int = 20
    schedule_mode: str = "interval"
    interval_minutes: int = 60
    cron: str = "0 * * * *"
    timezone: str = DEFAULT_TIMEZONE
    auto_organize: bool = False
    replace_versions: bool = True
    notify_channel_ids: list[int] = []


class FollowUpdate(BaseModel):
    feed_url: str | None = None
    title: str | None = None
    title_original: str | None = None
    poster: str | None = None
    bangumi_id: int | None = None
    mikan_bangumi_id: int | None = None
    subgroup_id: int | None = None
    subgroup_name: str | None = None
    weekday: int | None = None
    total_episodes: int | None = None
    status: str | None = None
    library_id: int | None = None
    save_subdir: str | None = None
    save_root: bool | None = None
    p115_folder_id: str | None = None
    p115_folder_path: str | None = None
    keyword: str | None = None
    regex: str | None = None
    min_size_mb: float | None = None
    max_per_run: int | None = None
    schedule_mode: str | None = None
    interval_minutes: int | None = None
    cron: str | None = None
    timezone: str | None = None
    auto_organize: bool | None = None
    replace_versions: bool | None = None
    notify_channel_ids: list[int] | None = None
    refresh: bool = False


class RssPreviewBody(BaseModel):
    feed_url: str = ""


def _today_weekday() -> int:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).weekday()


def _weekday(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="星期必须是 0–6") from None
    if number < 0 or number > 6:
        raise HTTPException(status_code=400, detail="星期必须是 0–6")
    return number


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    subscription = None
    if row.get("subscription_id"):
        subscription = query_one(
            "SELECT id, name, type, enabled, last_status, last_message, schedule "
            "FROM subscriptions WHERE id=?",
            (row["subscription_id"],),
        )
    schedule = normalize_schedule(subscription["schedule"]) if subscription else {}
    weekday = row.get("weekday")
    try:
        weekday = int(weekday) if weekday is not None else None
    except (TypeError, ValueError):
        weekday = None
    return {
        "id": row["id"],
        "bangumi_id": row.get("bangumi_id"),
        "title": row["title"],
        "title_original": row.get("title_original") or "",
        "poster": row.get("poster") or "",
        "feed_url": row.get("feed_url") or "",
        "mikan_bangumi_id": row.get("mikan_bangumi_id"),
        "subgroup_id": row.get("subgroup_id"),
        "subgroup_name": row.get("subgroup_name") or "",
        "weekday": weekday,
        "weekday_label": tracker.WEEKDAY_LABELS[weekday] if weekday is not None else "",
        "air_time": row.get("air_time") or "",
        "total_episodes": row.get("total_episodes"),
        "latest_episode": row.get("latest_episode"),
        "next_episode": row.get("next_episode"),
        "next_air_at": row.get("next_air_at"),
        "status": row.get("status") or "following",
        "last_error": row.get("last_error") or "",
        "last_synced_at": row.get("last_synced_at"),
        "subscription_id": row.get("subscription_id"),
        "subscription_name": subscription["name"] if subscription else "",
        "subscription_type": subscription["type"] if subscription else "",
        "subscription_enabled": bool(subscription["enabled"]) if subscription else False,
        "subscription_last_status": subscription["last_status"] if subscription else "",
        "schedule_mode": schedule.get("mode") or "interval",
        "check_interval_minutes": (
            int(schedule.get("interval_minutes") or 60)
            if schedule.get("mode") == "interval"
            else None
        ),
        "cron": schedule.get("cron") or "",
        "timezone": schedule.get("timezone") or DEFAULT_TIMEZONE,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _find_existing(
    bangumi_id: int | None,
    feed_url: str,
    subgroup_id: int | None,
    mikan_bangumi_id: int | None,
) -> dict[str, Any] | None:
    if mikan_bangumi_id is not None:
        row = query_one(
            "SELECT * FROM bangumi_follows WHERE mikan_bangumi_id=? ORDER BY id LIMIT 1",
            (mikan_bangumi_id,),
        )
        if row:
            return row
    if bangumi_id is not None:
        row = query_one(
            "SELECT * FROM bangumi_follows WHERE bangumi_id=? ORDER BY id LIMIT 1",
            (bangumi_id,),
        )
        if row:
            return row
    if feed_url:
        row = query_one(
            "SELECT * FROM bangumi_follows WHERE feed_url=? ORDER BY id LIMIT 1",
            (feed_url,),
        )
        if row:
            return row
    return None


def _require_library(library_id: int | None) -> int:
    if not library_id or not query_one("SELECT id FROM libraries WHERE id=?", (library_id,)):
        raise HTTPException(status_code=400, detail="请选择要整理到的影视库")
    return int(library_id)


def _map_folder_selection(
    library_id: int,
    save_subdir: str,
    save_root: bool,
    folder_id: str,
    folder_path: str,
) -> tuple[str, bool]:
    folder_id = (folder_id or "").strip()
    folder_path = (folder_path or "").strip().strip("/")
    if not folder_id:
        return save_subdir, save_root
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=400, detail="请选择要整理到的影视库")
    if not folder_path:
        raise HTTPException(status_code=400, detail="请选择有效的 115 目录")
    relative = map_cloud_folder(library["root_path"] or "", "", folder_path)
    if relative is None:
        raise HTTPException(
            status_code=400,
            detail="所选目录不在影视库范围内，请选择影视库根目录或其子目录",
        )
    return relative, not relative


def _rss_config(
    name: str,
    feed_url: str,
    save_subdir: str,
    save_root: bool,
    keyword: str,
    regex: str,
    min_size_mb: float,
    max_per_run: int,
    *,
    p115_folder_id: str = "",
    p115_folder_path: str = "",
) -> dict[str, Any]:
    try:
        return normalize_rss_config(
            {
                "feed_urls": [feed_url],
                "save_subdir": save_subdir,
                "save_root": save_root,
                "p115_folder_id": p115_folder_id,
                "p115_folder_path": p115_folder_path,
                "keyword": keyword,
                "regex": regex,
                "min_size_mb": min_size_mb,
                "max_per_run": max_per_run,
            },
            name,
        )
    except RssError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _rss_schedule(
    mode: str | None,
    interval_minutes: int | None,
    base: dict[str, Any] | None = None,
    *,
    cron: str | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    raw = dict(base or {})
    raw["mode"] = "cron" if str(mode or "interval") == "cron" else "interval"
    if interval_minutes is not None:
        raw["interval_minutes"] = int(interval_minutes)
    if cron is not None:
        raw["cron"] = cron
    if timezone_name:
        raw["timezone"] = timezone_name
    schedule = normalize_schedule(raw)
    if schedule["mode"] == "cron":
        try:
            CronExpression(schedule["cron"])
        except CronError as exc:
            raise HTTPException(status_code=400, detail=f"cron 表达式无效: {exc}") from exc
    return schedule


def _create_rss_subscription(
    name: str,
    library_id: int,
    feed_url: str,
    config: dict[str, Any],
    schedule: dict[str, Any],
    auto_organize: bool,
    replace_versions: bool,
    notify_channel_ids: list[int],
) -> int:
    library_id = _require_library(library_id)
    subscription_id = execute(
        "INSERT INTO subscriptions(name, type, enabled, library_id, config, schedule, "
        "auto_organize, replace_versions, notify_channel_ids, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            name[:80],
            "rss",
            1,
            library_id,
            dumps(config),
            dumps(schedule),
            int(bool(auto_organize)),
            int(bool(replace_versions)),
            dumps([int(value) for value in notify_channel_ids]),
            now(),
            now(),
        ),
    )
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    next_at = next_run_for_subscription(row) if row else None
    execute(
        "UPDATE subscriptions SET next_run_at=?, updated_at=? WHERE id=?",
        (next_at, now(), subscription_id),
    )
    return subscription_id


def _update_rss_subscription(
    subscription_id: int,
    *,
    name: str,
    library_id: int,
    feed_url: str,
    config: dict[str, Any],
    schedule: dict[str, Any],
    auto_organize: bool,
    replace_versions: bool,
    notify_channel_ids: list[int],
) -> None:
    existing = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    if not existing or existing.get("type") != "rss":
        raise HTTPException(status_code=404, detail="关联的 RSS 订阅不存在")
    library_id = _require_library(library_id)
    if int(existing["library_id"]) != int(library_id):
        execute("DELETE FROM subscription_entries WHERE subscription_id=?", (subscription_id,))
    execute(
        "UPDATE subscriptions SET name=?, library_id=?, config=?, schedule=?, "
        "auto_organize=?, replace_versions=?, notify_channel_ids=?, updated_at=? WHERE id=?",
        (
            name[:80],
            library_id,
            dumps(config),
            dumps(schedule),
            int(bool(auto_organize)),
            int(bool(replace_versions)),
            dumps([int(value) for value in notify_channel_ids]),
            now(),
            subscription_id,
        ),
    )
    row = query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
    next_at = next_run_for_subscription(row) if row else None
    execute(
        "UPDATE subscriptions SET next_run_at=?, updated_at=? WHERE id=?",
        (next_at, now(), subscription_id),
    )


@router.get("/follows")
def list_follows():
    rows = query_all(
        "SELECT * FROM bangumi_follows ORDER BY "
        "CASE WHEN status='following' THEN 0 ELSE 1 END, weekday IS NULL, weekday, air_time, id"
    )
    items = [_serialize(row) for row in rows]
    return {
        "items": items,
        "today_weekday": _today_weekday(),
        "counts": {
            "following": sum(1 for item in items if item["status"] == "following"),
            "paused": sum(1 for item in items if item["status"] == "paused"),
            "completed": sum(1 for item in items if item["status"] == "completed"),
        },
    }


@router.get("/calendar")
def calendar(force: bool = False):
    try:
        data = tracker.calendar(force=force)
    except tracker.BangumiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = query_all(
        "SELECT id, bangumi_id, mikan_bangumi_id, status FROM bangumi_follows"
    )
    by_mikan = {
        int(row["mikan_bangumi_id"]): row
        for row in rows
        if row.get("mikan_bangumi_id") is not None
    }
    by_bangumi = {
        int(row["bangumi_id"]): row for row in rows if row.get("bangumi_id") is not None
    }
    days = []
    for day in data["days"]:
        items = []
        for item in day["items"]:
            follow = None
            if item.get("mikan_id") is not None:
                follow = by_mikan.get(int(item["mikan_id"]))
            if not follow and item.get("bangumi_id") is not None:
                follow = by_bangumi.get(int(item["bangumi_id"]))
            items.append(
                {
                    **item,
                    "followed": bool(follow),
                    "follow_id": follow["id"] if follow else None,
                    "follow_status": follow["status"] if follow else "",
                }
            )
        days.append({**day, "items": items})
    return {
        **data,
        "days": days,
        "today_weekday": _today_weekday(),
    }


@router.get("/mikan/{mikan_id}")
def mikan_detail(mikan_id: int):
    try:
        detail = tracker.mikan_detail(mikan_id)
    except tracker.BangumiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    detail["total_episodes"] = None
    bangumi_id = detail.get("bangumi_id")
    if bangumi_id:
        try:
            detail["total_episodes"] = tracker.subject(
                int(bangumi_id), timeout=8.0
            ).get("total_episodes")
        except tracker.BangumiError:
            pass
    return detail


@router.post("/rss/preview")
def rss_preview(body: RssPreviewBody):
    try:
        return tracker.preview_feed(body.feed_url)
    except tracker.BangumiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _resolve_follow(body: FollowCreate) -> dict[str, Any]:
    mikan_id = int(body.mikan_bangumi_id) if body.mikan_bangumi_id else None
    detail: dict[str, Any] = {}
    if mikan_id:
        try:
            detail = tracker.mikan_detail(mikan_id)
        except tracker.BangumiError:
            detail = {}
    feed_url = body.feed_url.strip() or str(detail.get("feed_url") or "")
    if not feed_url:
        raise HTTPException(status_code=400, detail="请选择字幕组或填写蜜柑 RSS 地址")
    try:
        feed = tracker.parse_follow_feed(feed_url)
    except tracker.BangumiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mikan_id = mikan_id or feed.get("mikan_bangumi_id")
    if mikan_id and not detail:
        try:
            detail = tracker.mikan_detail(int(mikan_id))
        except tracker.BangumiError:
            detail = {}
    bangumi_id = int(body.bangumi_id) if body.bangumi_id else detail.get("bangumi_id")
    title = (
        body.title.strip()
        or str(detail.get("title") or "")
        or str(feed.get("title") or "")
    )
    if not title:
        raise HTTPException(status_code=400, detail="无法识别番剧名称，请手动填写")
    info: dict[str, Any] = {}
    if bangumi_id and (
        body.total_episodes is None
        or not (body.title_original or detail.get("title_original"))
    ):
        try:
            info = tracker.subject(int(bangumi_id))
        except tracker.BangumiError:
            info = {}
    total_episodes = (
        body.total_episodes if body.total_episodes is not None else info.get("total_episodes")
    )
    poster = (
        body.poster.strip()
        or str(detail.get("poster") or "")
        or str(feed.get("poster") or "")
        or str(info.get("poster") or "")
    )
    weekday = body.weekday if body.weekday is not None else detail.get("weekday")
    if weekday is None:
        weekday = feed.get("weekday")
    return {
        "feed_url": feed_url,
        "mikan_id": int(mikan_id) if mikan_id else None,
        "bangumi_id": int(bangumi_id) if bangumi_id else None,
        "title": title[:300],
        "title_original": str(
            body.title_original or detail.get("title_original") or info.get("title_original") or ""
        )[:300],
        "poster": poster[:1000],
        "weekday": _weekday(weekday),
        "total_episodes": int(total_episodes) if total_episodes is not None else None,
        "subgroup_id": (
            int(body.subgroup_id) if body.subgroup_id is not None else feed.get("subgroup_id")
        ),
        "subgroup_name": body.subgroup_name.strip()
        or str(feed.get("subgroup_name") or ""),
        "latest_episode": feed.get("latest_episode"),
    }


@router.post("/follows")
def create_follow(body: FollowCreate):
    data = _resolve_follow(body)
    title = str(data["title"])
    feed_url = str(data["feed_url"])
    save_root = (
        bool(body.save_root)
        if body.save_root is not None
        else not body.save_subdir.strip()
    )
    save_subdir, save_root = _map_folder_selection(
        body.library_id,
        body.save_subdir,
        save_root,
        body.p115_folder_id,
        body.p115_folder_path,
    )
    config = _rss_config(
        title,
        feed_url,
        save_subdir,
        save_root,
        body.keyword,
        body.regex,
        body.min_size_mb,
        body.max_per_run,
        p115_folder_id=body.p115_folder_id,
        p115_folder_path=body.p115_folder_path,
    )
    schedule = _rss_schedule(
        body.schedule_mode,
        body.interval_minutes,
        cron=body.cron,
        timezone_name=body.timezone,
    )
    existing = _find_existing(
        data["bangumi_id"],
        feed_url,
        data["subgroup_id"],
        data["mikan_id"],
    )
    subscription_id = (
        int(existing["subscription_id"])
        if existing and existing.get("subscription_id")
        else None
    )
    if subscription_id:
        _update_rss_subscription(
            subscription_id,
            name=title,
            library_id=body.library_id,
            feed_url=feed_url,
            config=config,
            schedule=schedule,
            auto_organize=body.auto_organize,
            replace_versions=body.replace_versions,
            notify_channel_ids=body.notify_channel_ids,
        )
    else:
        subscription_id = _create_rss_subscription(
            title,
            body.library_id,
            feed_url,
            config,
            schedule,
            body.auto_organize,
            body.replace_versions,
            body.notify_channel_ids,
        )

    latest = data["latest_episode"]
    total = data["total_episodes"]
    weekday = data["weekday"]
    if existing:
        current = existing.get("latest_episode")
        try:
            if current is not None and (latest is None or float(current) > float(latest)):
                latest = current
        except (TypeError, ValueError):
            pass
        total = total if total is not None else existing.get("total_episodes")
        if weekday is None:
            weekday = _weekday(existing.get("weekday"))
        status = str(existing.get("status") or "following")
        if status != "paused" and total is not None and latest is not None:
            if float(latest) >= int(total):
                status = "completed"
            elif status == "completed":
                status = "following"
        next_episode = None if status == "completed" or latest is None else float(latest) + 1
        execute(
            "UPDATE bangumi_follows SET title=?, title_original=?, poster=?, feed_url=?, "
            "bangumi_id=?, mikan_bangumi_id=?, subgroup_id=?, subgroup_name=?, weekday=?, "
            "air_time='', total_episodes=?, latest_episode=?, next_episode=?, next_air_at=NULL, "
            "status=?, subscription_id=?, last_error='', last_synced_at=?, updated_at=? "
            "WHERE id=?",
            (
                title,
                str(data["title_original"] or existing.get("title_original") or ""),
                str(data["poster"] or existing.get("poster") or ""),
                feed_url,
                data["bangumi_id"],
                data["mikan_id"],
                data["subgroup_id"],
                str(data["subgroup_name"] or existing.get("subgroup_name") or ""),
                weekday,
                total,
                latest,
                next_episode,
                status,
                subscription_id,
                now(),
                now(),
                existing["id"],
            ),
        )
        follow_id = int(existing["id"])
    else:
        status = "following"
        if total is not None and latest is not None and float(latest) >= int(total):
            status = "completed"
        next_episode = (
            None
            if status == "completed" or latest is None
            else float(latest) + 1
        )
        follow_id = execute(
            "INSERT INTO bangumi_follows(subscription_id, bangumi_id, title, title_original, "
            "poster, mikan_bangumi_id, subgroup_id, subgroup_name, weekday, air_time, "
            "total_episodes, latest_episode, next_episode, next_air_at, status, feed_url, "
            "last_error, last_synced_at, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,'',?,?,?,NULL,?,?,'',?,?,?)",
            (
                subscription_id,
                data["bangumi_id"],
                title,
                data["title_original"],
                data["poster"],
                data["mikan_id"],
                data["subgroup_id"],
                data["subgroup_name"],
                weekday,
                total,
                latest,
                next_episode,
                status,
                feed_url,
                now(),
                now(),
                now(),
            ),
        )
    tracker.ensure_subscription_feed(subscription_id, feed_url)
    # 新添加追更时立即排队一次，避免要等满一个检查间隔才首次转存
    execute(
        "UPDATE subscriptions SET next_run_at=?, updated_at=? WHERE id=? AND enabled=1",
        (now(), now(), subscription_id),
    )
    row = query_one("SELECT * FROM bangumi_follows WHERE id=?", (follow_id,))
    return _serialize(row)


@router.post("/follows/refresh")
def refresh_all():
    return tracker.refresh_follows()


@router.put("/follows/{follow_id}")
def update_follow(follow_id: int, body: FollowUpdate):
    row = query_one("SELECT * FROM bangumi_follows WHERE id=?", (follow_id,))
    if not row:
        raise HTTPException(status_code=404, detail="追更记录不存在")
    provided = body.model_fields_set
    title = str(body.title if "title" in provided and body.title is not None else row["title"])
    if not title.strip():
        raise HTTPException(status_code=400, detail="番剧名称不能为空")
    title = title.strip()[:300]
    title_original = str(
        body.title_original
        if "title_original" in provided and body.title_original is not None
        else row.get("title_original") or ""
    )
    poster = str(
        body.poster
        if "poster" in provided and body.poster is not None
        else row.get("poster") or ""
    )
    feed_url = str(
        body.feed_url
        if "feed_url" in provided and body.feed_url is not None
        else row.get("feed_url") or ""
    ).strip()
    if not feed_url:
        raise HTTPException(status_code=400, detail="请填写蜜柑 RSS 地址")
    if not feed_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="请填写有效的 RSS 地址")
    weekday = (
        _weekday(body.weekday)
        if "weekday" in provided
        else _weekday(row.get("weekday"))
    )
    total_episodes = (
        body.total_episodes
        if "total_episodes" in provided
        else row.get("total_episodes")
    )
    bangumi_id = (
        body.bangumi_id if "bangumi_id" in provided else row.get("bangumi_id")
    )
    if total_episodes is None and bangumi_id:
        try:
            total_episodes = tracker.subject(int(bangumi_id)).get("total_episodes")
        except tracker.BangumiError:
            pass
    mikan_id = (
        body.mikan_bangumi_id
        if "mikan_bangumi_id" in provided
        else row.get("mikan_bangumi_id")
    )
    subgroup_id = (
        body.subgroup_id if "subgroup_id" in provided else row.get("subgroup_id")
    )
    subgroup_name = str(
        body.subgroup_name
        if "subgroup_name" in provided and body.subgroup_name is not None
        else row.get("subgroup_name") or ""
    )
    status = (
        str(body.status)
        if "status" in provided and body.status is not None
        else str(row.get("status") or "following")
    )
    if status not in FOLLOW_STATUSES:
        raise HTTPException(status_code=400, detail="追更状态无效")
    status_explicit = "status" in provided and body.status is not None
    if (
        not status_explicit
        and status != "paused"
        and total_episodes is not None
        and row.get("latest_episode") is not None
    ):
        if float(row["latest_episode"]) >= int(total_episodes):
            status = "completed"
        elif status == "completed":
            status = "following"
    latest = row.get("latest_episode")
    next_episode = (
        None if status == "completed" or latest is None else float(latest) + 1
    )

    subscription_id = int(row["subscription_id"]) if row.get("subscription_id") else None
    subscription = (
        query_one("SELECT * FROM subscriptions WHERE id=?", (subscription_id,))
        if subscription_id
        else None
    )
    if subscription_id and not subscription:
        subscription_id = None
    old_config = loads(subscription["config"], {}) if subscription else {}
    old_schedule = normalize_schedule(subscription["schedule"]) if subscription else {}
    save_subdir = (
        body.save_subdir
        if "save_subdir" in provided and body.save_subdir is not None
        else str(old_config.get("save_subdir") or "")
    )
    if "save_root" in provided and body.save_root is not None:
        save_root = bool(body.save_root)
    elif "save_subdir" in provided:
        save_root = not save_subdir.strip()
    else:
        save_root = bool(old_config.get("save_root")) or not save_subdir.strip()
    folder_id = (
        str(body.p115_folder_id or "").strip()
        if "p115_folder_id" in provided
        else str(old_config.get("p115_folder_id") or "")
    )
    folder_path = (
        str(body.p115_folder_path or "").strip().strip("/")
        if "p115_folder_path" in provided
        else str(old_config.get("p115_folder_path") or "")
    )
    if "p115_folder_id" in provided and not folder_id:
        folder_path = ""
    library_id = (
        body.library_id
        if "library_id" in provided and body.library_id is not None
        else int(subscription["library_id"]) if subscription else None
    )
    save_subdir, save_root = _map_folder_selection(
        library_id or 0, save_subdir, save_root, folder_id, folder_path
    )
    keyword = (
        body.keyword
        if "keyword" in provided and body.keyword is not None
        else str(old_config.get("keyword") or "")
    )
    regex = (
        body.regex
        if "regex" in provided and body.regex is not None
        else str(old_config.get("regex") or "")
    )
    min_size = (
        body.min_size_mb
        if "min_size_mb" in provided and body.min_size_mb is not None
        else float(old_config.get("min_size_mb") or 0)
    )
    max_per_run = (
        body.max_per_run
        if "max_per_run" in provided and body.max_per_run is not None
        else int(
            old_config["max_per_run"]
            if old_config.get("max_per_run") is not None
            else 20
        )
    )
    config = _rss_config(
        title,
        feed_url,
        save_subdir,
        save_root,
        keyword,
        regex,
        min_size,
        max_per_run,
        p115_folder_id=folder_id,
        p115_folder_path=folder_path,
    )
    mode = (
        body.schedule_mode
        if "schedule_mode" in provided and body.schedule_mode is not None
        else str(old_schedule.get("mode") or "interval")
    )
    interval = (
        body.interval_minutes
        if "interval_minutes" in provided and body.interval_minutes is not None
        else int(old_schedule.get("interval_minutes") or 60)
    )
    cron = (
        body.cron
        if "cron" in provided and body.cron is not None
        else str(old_schedule.get("cron") or "0 * * * *")
    )
    timezone_name = (
        body.timezone
        if "timezone" in provided and body.timezone is not None
        else str(old_schedule.get("timezone") or DEFAULT_TIMEZONE)
    )
    schedule = _rss_schedule(
        mode,
        interval,
        old_schedule,
        cron=cron,
        timezone_name=timezone_name,
    )
    auto_organize = (
        bool(body.auto_organize)
        if "auto_organize" in provided and body.auto_organize is not None
        else bool(subscription["auto_organize"]) if subscription else False
    )
    replace_versions = (
        bool(body.replace_versions)
        if "replace_versions" in provided and body.replace_versions is not None
        else bool(subscription["replace_versions"]) if subscription else True
    )
    notify_ids = (
        [int(value) for value in body.notify_channel_ids]
        if "notify_channel_ids" in provided and body.notify_channel_ids is not None
        else loads(subscription["notify_channel_ids"], []) if subscription else []
    )
    if subscription_id:
        _update_rss_subscription(
            subscription_id,
            name=title,
            library_id=library_id or 0,
            feed_url=feed_url,
            config=config,
            schedule=schedule,
            auto_organize=auto_organize,
            replace_versions=replace_versions,
            notify_channel_ids=notify_ids,
        )
    else:
        subscription_id = _create_rss_subscription(
            title,
            library_id or 0,
            feed_url,
            config,
            schedule,
            auto_organize,
            replace_versions,
            notify_ids,
        )

    execute(
        "UPDATE bangumi_follows SET title=?, title_original=?, poster=?, feed_url=?, "
        "bangumi_id=?, mikan_bangumi_id=?, subgroup_id=?, subgroup_name=?, weekday=?, "
        "air_time='', total_episodes=?, next_episode=?, next_air_at=NULL, status=?, "
        "subscription_id=?, updated_at=? WHERE id=?",
        (
            title,
            title_original[:300],
            poster[:1000],
            feed_url,
            bangumi_id,
            mikan_id,
            subgroup_id,
            subgroup_name[:200],
            weekday,
            int(total_episodes) if total_episodes is not None else None,
            next_episode,
            status,
            subscription_id,
            now(),
            follow_id,
        ),
    )
    tracker.ensure_subscription_feed(subscription_id, feed_url)
    if body.refresh and feed_url:
        row = query_one("SELECT * FROM bangumi_follows WHERE id=?", (follow_id,))
        row = tracker.refresh_follow(row)
    row = query_one("SELECT * FROM bangumi_follows WHERE id=?", (follow_id,))
    return _serialize(row)


@router.post("/follows/{follow_id}/refresh")
def refresh_follow(follow_id: int):
    row = query_one("SELECT * FROM bangumi_follows WHERE id=?", (follow_id,))
    if not row:
        raise HTTPException(status_code=404, detail="追更记录不存在")
    if not row.get("feed_url"):
        raise HTTPException(status_code=400, detail="该追更没有 RSS 地址")
    row = tracker.refresh_follow(row)
    return _serialize(row)


@router.delete("/follows/{follow_id}")
def delete_follow(follow_id: int):
    row = query_one(
        "SELECT id, subscription_id FROM bangumi_follows WHERE id=?", (follow_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="追更记录不存在")
    subscription_id = int(row["subscription_id"]) if row.get("subscription_id") else 0
    if subscription_id:
        active = query_one(
            "SELECT id FROM subscription_runs WHERE subscription_id=? "
            "AND status IN ('queued','running') LIMIT 1",
            (subscription_id,),
        )
        if active:
            raise HTTPException(
                status_code=409, detail="对应的 RSS 订阅正在运行，请稍后再删除"
            )
        shared = query_one(
            "SELECT id FROM bangumi_follows WHERE subscription_id=? AND id<>?",
            (subscription_id, follow_id),
        )
        with db() as conn:
            conn.execute("DELETE FROM bangumi_follows WHERE id=?", (follow_id,))
            if not shared:
                conn.execute("DELETE FROM subscriptions WHERE id=?", (subscription_id,))
        return {"ok": True}
    execute("DELETE FROM bangumi_follows WHERE id=?", (follow_id,))
    return {"ok": True}
