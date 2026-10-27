"""订阅计划计算回归测试。

运行：cd backend && .venv/bin/python ../tests/test_scheduling.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.scheduling import (  # noqa: E402
    CronExpression,
    next_run_iso,
    normalize_schedule,
    parse_deadline,
)


def iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def main() -> int:
    failures: list[str] = []

    def expect(name: str, actual, expected) -> None:
        if actual != expected:
            failures.append(f"{name}: expected {expected!r}, got {actual!r}")

    base = iso("2026-10-26T00:00:00+00:00")
    interval = {"mode": "interval", "interval_minutes": 60, "timezone": "Asia/Shanghai"}
    expect("interval", next_run_iso(interval, after=base), "2026-10-26T01:00:00+00:00")

    window = {**interval, "window_start": "08:00", "window_end": "23:00"}
    expect(
        "window inside",
        next_run_iso(window, after=iso("2026-10-25T23:30:00+00:00")),
        "2026-10-26T00:30:00+00:00",
    )
    expect(
        "window next day",
        next_run_iso(window, after=iso("2026-10-26T14:30:00+00:00")),
        "2026-10-27T00:00:00+00:00",
    )

    overnight = {**interval, "window_start": "22:00", "window_end": "02:00"}
    expect(
        "window overnight",
        next_run_iso(overnight, after=iso("2026-10-26T06:30:00+00:00")),
        "2026-10-26T14:00:00+00:00",
    )

    daily = {"mode": "cron", "cron": "0 9 * * *", "timezone": "Asia/Shanghai"}
    expect("cron daily", next_run_iso(daily, after=base), "2026-10-26T01:00:00+00:00")

    weekly = {"mode": "cron", "cron": "0 9 * * mon", "timezone": "Asia/Shanghai"}
    expect(
        "cron weekday",
        next_run_iso(weekly, after=iso("2026-10-25T12:00:00+00:00")),
        "2026-10-26T01:00:00+00:00",
    )

    macro = {"mode": "cron", "cron": "@daily", "timezone": "Asia/Shanghai"}
    expect("cron macro", next_run_iso(macro, after=base), "2026-10-26T16:00:00+00:00")

    invalid = {"mode": "cron", "cron": "not a cron", "timezone": "Asia/Shanghai"}
    expect("cron invalid", next_run_iso(invalid, after=base), None)

    deadline = parse_deadline("2026-10-26", "Asia/Shanghai")
    expect("deadline before", next_run_iso(window, after=base, deadline=deadline), "2026-10-26T01:00:00+00:00")
    expect(
        "deadline expired",
        next_run_iso(window, after=iso("2026-10-26T14:30:00+00:00"), deadline=deadline),
        None,
    )

    normalized = normalize_schedule({"interval_minutes": 1, "window_start": "7:05"})
    expect("normalize interval", normalized["interval_minutes"], 5)
    expect("normalize window start", normalized["window_start"], "07:05")
    expect("normalize incomplete window", normalized["window_end"], "")

    try:
        CronExpression("61 * * * *")
        failures.append("cron minute range should fail")
    except ValueError:
        pass

    cron = CronExpression("*/15 8-10 * * mon-fri")
    candidate = cron.next_after(iso("2026-10-26T00:00:00+08:00"))
    expect("cron detail", candidate.isoformat(), "2026-10-26T08:00:00+08:00")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("scheduling cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
