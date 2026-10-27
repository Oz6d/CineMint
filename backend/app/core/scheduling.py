"""订阅计划的解析与下一次运行时间计算。

支持两种模式：
- interval：固定间隔 + 可选每日时段；
- cron：标准 5 段表达式（分 时 日 月 周）。

所有对外时间均以 UTC ISO 字符串存储，展示时由前端按本地时区处理。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_INTERVAL_MINUTES = 60
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 7 * 24 * 60
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_CRON = "0 * * * *"

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_WEEKDAY_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}
_MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


class CronError(ValueError):
    pass


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def _hhmm(raw: Any) -> str:
    value = str(raw or "").strip()
    match = _HHMM_RE.match(value)
    if not match:
        return ""
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def normalize_schedule(raw: Any) -> dict[str, Any]:
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    mode = "cron" if str(data.get("mode") or "") == "cron" else "interval"
    try:
        interval = int(data.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL_MINUTES
    interval = max(MIN_INTERVAL_MINUTES, min(interval, MAX_INTERVAL_MINUTES))
    window_start = _hhmm(data.get("window_start"))
    window_end = _hhmm(data.get("window_end"))
    cron = str(data.get("cron") or DEFAULT_CRON).strip()[:100] or DEFAULT_CRON
    timezone_name = str(data.get("timezone") or DEFAULT_TIMEZONE).strip()[:64]
    _zone(timezone_name)
    return {
        "mode": mode,
        "interval_minutes": interval,
        "window_start": window_start,
        "window_end": window_end,
        "cron": cron,
        "timezone": timezone_name,
    }


def _field(
    raw: str,
    low: int,
    high: int,
    names: dict[str, int] | None = None,
    *,
    weekday: bool = False,
) -> set[int]:
    text = (raw or "*").strip().lower()
    if text in {"", "?"}:
        text = "*"
    values: set[int] = set()

    def value_of(token: str) -> int:
        token = token.strip().lower()
        if names and token in names:
            return names[token]
        try:
            return int(token)
        except ValueError as exc:
            raise CronError(f"无法识别的字段值: {token}") from exc

    for part in text.split(","):
        part = part.strip()
        if not part:
            raise CronError("cron 字段中包含空项")
        step = 1
        if "/" in part:
            part, step_raw = part.split("/", 1)
            try:
                step = int(step_raw)
            except ValueError as exc:
                raise CronError(f"无法识别的步长: {step_raw}") from exc
            if step <= 0:
                raise CronError("步长必须大于 0")
        if part in {"*", "?"}:
            start, end = low, high
        elif "-" in part:
            left, right = part.split("-", 1)
            start, end = value_of(left), value_of(right)
        else:
            start = end = value_of(part)
        if start < low or end > high or start > end:
            raise CronError(f"cron 字段超出范围: {part}")
        values.update(range(start, end + 1, step))

    if weekday and 7 in values:
        values.discard(7)
        values.add(0)
    return values


class CronExpression:
    def __init__(self, expression: str) -> None:
        text = (expression or "").strip()
        if text.lower() in _MACROS:
            text = _MACROS[text.lower()]
        fields = text.split()
        if len(fields) != 5:
            raise CronError("cron 需要 5 段：分 时 日 月 周")
        self.minutes = _field(fields[0], 0, 59)
        self.hours = _field(fields[1], 0, 23)
        self.days = _field(fields[2], 1, 31)
        self.months = _field(fields[3], 1, 12, _MONTH_NAMES)
        self.weekdays = _field(fields[4], 0, 7, _WEEKDAY_NAMES, weekday=True)
        self.days_star = fields[2].strip() in {"*", "?"}
        self.weekdays_star = fields[4].strip() in {"*", "?"}

    def day_matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom_match = day.day in self.days
        cron_dow = (day.weekday() + 1) % 7
        dow_match = cron_dow in self.weekdays
        if self.days_star and self.weekdays_star:
            return True
        if self.days_star:
            return dow_match
        if self.weekdays_star:
            return dom_match
        return dom_match or dow_match

    def next_after(self, local_after: datetime) -> datetime | None:
        start = local_after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = start.date()
        zone = local_after.tzinfo
        hours = sorted(self.hours)
        minutes = sorted(self.minutes)
        for offset in range(367):
            current = day + timedelta(days=offset)
            if not self.day_matches(current):
                continue
            for hour in hours:
                if offset == 0 and hour < start.hour:
                    continue
                for minute in minutes:
                    if offset == 0 and hour == start.hour and minute < start.minute:
                        continue
                    candidate = datetime.combine(current, time(hour, minute), tzinfo=zone)
                    if candidate >= start:
                        return candidate
        return None


def parse_deadline(raw: Any, timezone_name: str | None = None) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    zone = _zone(timezone_name)
    try:
        if _DATE_RE.match(value):
            day = date.fromisoformat(value)
            local = datetime.combine(day, time(23, 59, 59), tzinfo=zone)
            return local.astimezone(timezone.utc)
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


def _with_window(local_candidate: datetime, schedule: dict[str, Any], zone: ZoneInfo) -> datetime:
    start_raw = schedule.get("window_start") or ""
    end_raw = schedule.get("window_end") or ""
    if not start_raw or not end_raw:
        return local_candidate
    start_hour, start_minute = (int(part) for part in start_raw.split(":"))
    end_hour, end_minute = (int(part) for part in end_raw.split(":"))
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    current = local_candidate.hour * 60 + local_candidate.minute
    if start <= end:
        inside = start <= current <= end
    else:
        inside = current >= start or current <= end
    if inside:
        return local_candidate
    target_day = local_candidate.date() if current < start else local_candidate.date() + timedelta(days=1)
    return datetime.combine(target_day, time(start_hour, start_minute), tzinfo=zone)


def next_run_time(
    schedule: Any,
    after: datetime | None = None,
    deadline: datetime | None = None,
) -> datetime | None:
    normalized = normalize_schedule(schedule)
    zone = _zone(normalized["timezone"])
    now_utc = after or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    if normalized["mode"] == "cron":
        local_after = now_utc.astimezone(zone)
        try:
            candidate_local = CronExpression(normalized["cron"]).next_after(local_after)
        except CronError:
            return None
        if candidate_local is None:
            return None
        candidate = candidate_local.astimezone(timezone.utc)
    else:
        candidate = now_utc + timedelta(minutes=int(normalized["interval_minutes"]))
        candidate = _with_window(candidate.astimezone(zone), normalized, zone).astimezone(
            timezone.utc
        )

    if deadline and candidate > deadline:
        return None
    return candidate


def next_run_iso(
    schedule: Any,
    after: datetime | None = None,
    deadline: datetime | None = None,
) -> str | None:
    candidate = next_run_time(schedule, after=after, deadline=deadline)
    return candidate.astimezone(timezone.utc).isoformat(timespec="seconds") if candidate else None


def next_run_for_subscription(
    subscription: dict[str, Any],
    after: datetime | None = None,
) -> str | None:
    normalized = normalize_schedule(subscription.get("schedule"))
    deadline = parse_deadline(subscription.get("deadline"), normalized["timezone"])
    return next_run_iso(normalized, after=after, deadline=deadline)
