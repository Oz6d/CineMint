"""AI 调用 token 与费用统计。

OpenAI 兼容接口会在响应里返回 usage。这里按 DeepSeek / OpenAI 的常见字段
拆分「缓存命中输入 / 未命中输入 / 输出」，再叠加可配置的峰谷倍率：

    cost = (缓存命中 tokens × 缓存价
            + 未命中 tokens × 输入价
            + 输出 tokens × 输出价) × 时段倍率

单价按调用时快照保存，后续调价不会改写历史记录。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import execute, get_setting, now, query_all, query_one

DEFAULT_INPUT_PRICE = 0.15
DEFAULT_OUTPUT_PRICE = 0.60
DEFAULT_PEAK_WINDOWS = "01:00-04:00,06:00-10:00"
RECENT_LIMIT = 30


def _date_range(start: str, end: str) -> list[str]:
    current = datetime.fromisoformat(start).date()
    last = datetime.fromisoformat(end).date()
    values: list[str] = []
    while current <= last:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return values


# 2026 年国务院办公厅放假安排（2025-11-04 发布）。
# 节假日后续年份需要手动更新；周末另行判断。
DEFAULT_CN_HOLIDAYS = ",".join(
    [
        *_date_range("2026-01-01", "2026-01-03"),  # 元旦
        *_date_range("2026-02-15", "2026-02-23"),  # 春节
        *_date_range("2026-04-04", "2026-04-06"),  # 清明
        *_date_range("2026-05-01", "2026-05-05"),  # 劳动节
        *_date_range("2026-06-19", "2026-06-21"),  # 端午
        *_date_range("2026-09-25", "2026-09-27"),  # 中秋
        *_date_range("2026-10-01", "2026-10-07"),  # 国庆
    ]
)


def _holiday_dates() -> set[str]:
    raw = str(get_setting("ai_cn_holidays", DEFAULT_CN_HOLIDAYS) or "")
    values: set[str] = set()
    for token in re.split(r"[\s,，;；]+", raw):
        token = token.strip()
        if not token:
            continue
        try:
            values.add(datetime.fromisoformat(token).date().isoformat())
        except ValueError:
            continue
    return values


def _float_setting(key: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(get_setting(key, default) or 0))
    except (TypeError, ValueError):
        return default


def _cached_price(input_price: float) -> float:
    """未单独配置缓存价时，按普通输入价计算，避免低估。"""
    raw = get_setting("ai_cached_input_price", "")
    if raw in ("", None):
        return input_price
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return input_price


def estimate_tokens(text: str) -> int:
    """服务商没返回 usage 时的粗略估算：中文按字，其他按 4 字符 / token。"""
    value = text or ""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", value))
    other = max(0, len(value) - cjk)
    return max(1, cjk + (other + 3) // 4)


def _parse_windows(value: str) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for part in (value or "").split(","):
        match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", part)
        if not match:
            continue
        start = int(match.group(1)) * 60 + int(match.group(2))
        end = int(match.group(3)) * 60 + int(match.group(4))
        if 0 <= start < 1440 and 0 <= end <= 1440 and start != end:
            windows.append((start, end))
    return windows


def is_peak_time(moment: datetime | None = None) -> bool:
    """按设置里的 UTC 峰时窗口判断；周末默认全天谷时。"""
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    if current.date().isoformat() in _holiday_dates():
        return False
    if bool(get_setting("ai_weekend_offpeak", True)) and current.weekday() >= 5:
        return False
    minutes = current.hour * 60 + current.minute
    windows = _parse_windows(
        str(get_setting("ai_peak_windows", "") or DEFAULT_PEAK_WINDOWS)
    )
    for start, end in windows:
        if start < end:
            if start <= minutes < end:
                return True
        elif minutes >= start or minutes < end:
            return True
    return False


def current_multiplier(moment: datetime | None = None) -> tuple[bool, float]:
    peak = is_peak_time(moment)
    if peak:
        return True, _float_setting("ai_peak_multiplier", 1.0)
    return False, _float_setting("ai_offpeak_multiplier", 1.0)


def record_ai_usage(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    context: str = "",
    file_name: str = "",
    estimated: bool = False,
    reported_cost: float | None = None,
    at: datetime | None = None,
) -> dict[str, Any]:
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    cached = max(0, min(int(cached_tokens or 0), prompt))
    uncached = max(0, prompt - cached)
    input_price = _float_setting("ai_input_price", DEFAULT_INPUT_PRICE)
    cached_price = _cached_price(input_price)
    output_price = _float_setting("ai_output_price", DEFAULT_OUTPUT_PRICE)
    mode = str(get_setting("ai_pricing_mode", "manual") or "manual")
    reported = None
    try:
        if reported_cost is not None and float(reported_cost) >= 0:
            reported = float(reported_cost)
    except (TypeError, ValueError):
        reported = None

    if mode == "provider" and reported is not None:
        peak, multiplier = False, 1.0
        cost = reported
        cost_source = "provider"
    else:
        if mode in {"openrouter", "litellm"}:
            peak, multiplier = False, 1.0
        else:
            peak, multiplier = current_multiplier(at)
        base_cost = (
            (cached / 1_000_000) * cached_price
            + (uncached / 1_000_000) * input_price
            + (completion / 1_000_000) * output_price
        )
        cost = base_cost * multiplier
        if mode in {"openrouter", "litellm"}:
            cost_source = mode
        elif mode == "provider":
            cost_source = "manual-fallback"
        else:
            cost_source = "manual"

    created_at = now()
    execute(
        "INSERT INTO ai_usage(created_at, model, prompt_tokens, completion_tokens, total_tokens, "
        "cached_tokens, uncached_tokens, input_price, cached_price, output_price, multiplier, peak, "
        "cost, reported_cost, cost_source, estimated, context, file_name) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            created_at,
            model or "",
            prompt,
            completion,
            prompt + completion,
            cached,
            uncached,
            input_price,
            cached_price,
            output_price,
            multiplier,
            1 if peak else 0,
            round(cost, 8),
            round(reported, 8) if reported is not None else 0,
            cost_source,
            1 if estimated else 0,
            context or "",
            file_name or "",
        ),
    )
    return {
        "created_at": created_at,
        "model": model or "",
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cached_tokens": cached,
        "uncached_tokens": uncached,
        "input_price": input_price,
        "cached_price": cached_price,
        "output_price": output_price,
        "multiplier": multiplier,
        "peak": peak,
        "cost": round(cost, 8),
        "reported_cost": round(reported, 8) if reported is not None else 0,
        "cost_source": cost_source,
        "estimated": bool(estimated),
    }


def _totals(where: str = "", params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = query_one(
        "SELECT COUNT(*) AS calls, "
        "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
        "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
        "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
        "COALESCE(SUM(cached_tokens), 0) AS cached_tokens, "
        "COALESCE(SUM(uncached_tokens), 0) AS uncached_tokens, "
        "COALESCE(SUM(cost), 0) AS cost "
        f"FROM ai_usage {where}",
        params,
    ) or {}
    return {
        "calls": int(row.get("calls") or 0),
        "prompt_tokens": int(row.get("prompt_tokens") or 0),
        "completion_tokens": int(row.get("completion_tokens") or 0),
        "total_tokens": int(row.get("total_tokens") or 0),
        "cached_tokens": int(row.get("cached_tokens") or 0),
        "uncached_tokens": int(row.get("uncached_tokens") or 0),
        "cost": round(float(row.get("cost") or 0), 8),
    }


def ai_usage_summary(days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days or 30), 3650))
    now_utc = datetime.now(timezone.utc)
    period_start = (now_utc - timedelta(days=days)).isoformat(timespec="seconds")
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(
        timespec="seconds"
    )

    by_model = [
        {
            "model": row.get("model") or "",
            "calls": int(row.get("calls") or 0),
            "prompt_tokens": int(row.get("prompt_tokens") or 0),
            "completion_tokens": int(row.get("completion_tokens") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
            "cached_tokens": int(row.get("cached_tokens") or 0),
            "uncached_tokens": int(row.get("uncached_tokens") or 0),
            "cost": round(float(row.get("cost") or 0), 8),
        }
        for row in query_all(
            "SELECT model, COUNT(*) AS calls, "
            "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
            "COALESCE(SUM(total_tokens), 0) AS total_tokens, "
            "COALESCE(SUM(cached_tokens), 0) AS cached_tokens, "
            "COALESCE(SUM(uncached_tokens), 0) AS uncached_tokens, "
            "COALESCE(SUM(cost), 0) AS cost "
            "FROM ai_usage WHERE created_at >= ? GROUP BY model ORDER BY cost DESC",
            (period_start,),
        )
    ]

    recent = [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "model": row["model"],
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": row["total_tokens"],
            "cached_tokens": row["cached_tokens"],
            "uncached_tokens": row["uncached_tokens"],
            "cost": round(float(row["cost"] or 0), 8),
            "reported_cost": round(float(row["reported_cost"] or 0), 8),
            "cost_source": row["cost_source"] or "manual",
            "multiplier": float(row["multiplier"] or 1),
            "peak": bool(row["peak"]),
            "estimated": bool(row["estimated"]),
            "context": row["context"],
            "file_name": row["file_name"],
        }
        for row in query_all(
            "SELECT id, created_at, model, prompt_tokens, completion_tokens, total_tokens, "
            "cached_tokens, uncached_tokens, cost, reported_cost, cost_source, multiplier, peak, "
            "estimated, context, file_name "
            "FROM ai_usage ORDER BY id DESC LIMIT ?",
            (RECENT_LIMIT,),
        )
    ]

    input_price = _float_setting("ai_input_price", DEFAULT_INPUT_PRICE)
    return {
        "days": days,
        "period": _totals("WHERE created_at >= ?", (period_start,)),
        "today": _totals("WHERE created_at >= ?", (today_start,)),
        "all_time": _totals(),
        "by_model": by_model,
        "recent": recent,
        "prices": {
            "input": input_price,
            "cached": _cached_price(input_price),
            "output": _float_setting("ai_output_price", DEFAULT_OUTPUT_PRICE),
            "peak_multiplier": _float_setting("ai_peak_multiplier", 1.0),
            "offpeak_multiplier": _float_setting("ai_offpeak_multiplier", 1.0),
            "peak_windows": str(get_setting("ai_peak_windows", "") or DEFAULT_PEAK_WINDOWS),
            "weekend_offpeak": bool(get_setting("ai_weekend_offpeak", True)),
        },
    }


def clear_ai_usage() -> None:
    execute("DELETE FROM ai_usage")
