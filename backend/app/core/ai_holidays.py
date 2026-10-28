"""中国法定节假日自动同步。

数据源：NateScarlet/holiday-cn（每日抓取国务院公告的公开 JSON）。
固定使用 jsDelivr 的 GitHub 镜像，按年份拉取，只取 isOffDay=true 的日期。
同步失败时保留现有日期，不影响 AI 计费。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..db import get_setting, now, set_setting

BASE_URLS = (
    "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://fastly.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{year}.json",
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json",
)
SOURCE_NAME = "NateScarlet/holiday-cn"


class HolidayError(RuntimeError):
    pass


def _fetch_year(year: int) -> tuple[list[str], str]:
    errors: list[str] = []
    for template in BASE_URLS:
        url = template.format(year=year)
        try:
            response = httpx.get(
                url,
                timeout=20,
                follow_redirects=True,
                headers={"User-Agent": "CineMint/0.1 (+holiday-sync)"},
            )
        except httpx.HTTPError as exc:
            errors.append(f"{url}: {exc}")
            continue
        if response.status_code == 404:
            errors.append(f"{year} 年节假日数据尚未发布")
            continue
        if response.status_code >= 400:
            errors.append(f"{url}: HTTP {response.status_code}")
            continue
        try:
            data = response.json()
        except ValueError:
            errors.append(f"{url}: 返回内容不是 JSON")
            continue
        days = data.get("days") if isinstance(data, dict) else None
        if not isinstance(days, list):
            errors.append(f"{url}: 数据格式无法识别")
            continue
        dates = sorted(
            {
                str(day.get("date") or "")
                for day in days
                if isinstance(day, dict) and day.get("isOffDay") and day.get("date")
            }
        )
        if dates:
            return dates, url
        errors.append(f"{url}: 没有放假日数据")
    raise HolidayError("; ".join(dict.fromkeys(errors))[:500] or "节假日同步失败")


def _split_dates(value: str) -> set[str]:
    return {
        token.strip()
        for token in str(value or "").replace("，", ",").replace(";", ",").split(",")
        if token.strip()
    }


def sync_holidays(years: list[int] | None = None) -> dict[str, Any]:
    current_year = datetime.now(timezone.utc).year
    targets = years or [current_year, current_year + 1]
    targets = sorted({int(year) for year in targets if 2020 <= int(year) <= 2100})
    if not targets:
        raise HolidayError("年份不合法")

    existing = _split_dates(get_setting("ai_cn_holidays", ""))
    sources: list[str] = []
    errors: list[str] = []
    for year in targets:
        try:
            dates, source_url = _fetch_year(year)
        except HolidayError as exc:
            errors.append(str(exc))
            continue
        existing = {date for date in existing if not date.startswith(f"{year}-")}
        existing.update(dates)
        sources.append(source_url)

    if not sources:
        raise HolidayError("; ".join(errors[:3]) or "节假日同步失败")

    value = ",".join(sorted(existing))
    updated_at = now()
    set_setting("ai_cn_holidays", value)
    set_setting("ai_cn_holidays_updated_at", updated_at)
    set_setting("ai_cn_holidays_source", ",".join(sources))
    return {
        "holidays": value,
        "years": targets,
        "updated_at": updated_at,
        "source": SOURCE_NAME,
        "warnings": errors,
    }
