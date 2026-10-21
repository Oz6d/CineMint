"""AI token 用量与费用统计的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_ai_usage.py
使用临时数据目录，不会写 data/cineflow.db。
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cineflow-ai-usage-")
os.environ["CINEFLOW_DATA_DIR"] = TEMP_DATA
os.environ["CINEFLOW_DEMO"] = "false"
os.environ["CINEFLOW_ADMIN_PASSWORD"] = "cineflow"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.ai_usage import (  # noqa: E402
    ai_usage_summary,
    clear_ai_usage,
    estimate_tokens,
    is_peak_time,
    record_ai_usage,
)
from app.core.metadata.ai import parse_with_ai  # noqa: E402
from app.main import app  # noqa: E402


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")
    return False


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        passed += check(name, actual, expected)

    expect("estimate ascii", estimate_tokens("hello"), 2)
    expect("estimate mixed", estimate_tokens("你好world"), 4)
    expect("estimate empty", estimate_tokens(""), 1)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cineflow"})
        expect("login", login.status_code, 200)

        saved = client.put(
            "/api/settings",
            json={"values": {"ai_input_price": 0.15, "ai_output_price": 0.60}},
        )
        expect("settings save", saved.status_code, 200)
        expect("settings input price", saved.json().get("ai_input_price"), 0.15)
        expect("settings output price", saved.json().get("ai_output_price"), 0.60)

        record = record_ai_usage(
            model="gpt-test",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            context="cross_validate",
            file_name="Movie.2023.mkv",
        )
        expect("record cost", record["cost"], 0.75)
        expect("record total tokens", record["total_tokens"], 2_000_000)

        summary = ai_usage_summary(days=30)
        expect("summary calls", summary["all_time"]["calls"], 1)
        expect("summary prompt tokens", summary["all_time"]["prompt_tokens"], 1_000_000)
        expect("summary completion tokens", summary["all_time"]["completion_tokens"], 1_000_000)
        expect("summary cost", summary["all_time"]["cost"], 0.75)
        expect("summary by model", summary["by_model"][0]["model"], "gpt-test")
        expect("summary recent file", summary["recent"][0]["file_name"], "Movie.2023.mkv")

        api_usage = client.get("/api/settings/ai-usage?days=30")
        expect("usage api status", api_usage.status_code, 200)
        expect("usage api calls", api_usage.json().get("all_time", {}).get("calls"), 1)
        expect("usage api cost", api_usage.json().get("all_time", {}).get("cost"), 0.75)

        # AI 返回 usage 时按真实 token 落库。
        body_with_usage = {
            "choices": [{"message": {"content": '{"title":"Movie","year":2023}'}}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 40,
            },
        }
        with (
            patch("app.core.metadata.ai.ai_available", return_value=True),
            patch(
                "app.core.metadata.ai._ai_config",
                return_value=("https://example.test/v1", "gpt-mock", "sk-test"),
            ),
            patch(
                "app.core.metadata.ai.httpx.post",
                return_value=FakeResponse(body_with_usage),
            ),
        ):
            result = parse_with_ai("Movie.2023.1080p.mkv")
        expect("parse title", result.get("title") if result else None, "Movie")
        summary = ai_usage_summary(days=30)
        expect("usage call recorded", summary["all_time"]["calls"], 2)
        expect("usage prompt recorded", summary["recent"][0]["prompt_tokens"], 120)
        expect("usage completion recorded", summary["recent"][0]["completion_tokens"], 30)
        expect("usage cached recorded", summary["recent"][0]["cached_tokens"], 80)
        expect("usage uncached recorded", summary["recent"][0]["uncached_tokens"], 40)
        expect("usage not estimated", summary["recent"][0]["estimated"], False)

        # 服务商没有 usage 时，按字符数估算并标记 estimated。
        body_without_usage = {
            "choices": [{"message": {"content": '{"title":"Another"}'}}],
        }
        with (
            patch("app.core.metadata.ai.ai_available", return_value=True),
            patch(
                "app.core.metadata.ai._ai_config",
                return_value=("https://example.test/v1", "gpt-mock", "sk-test"),
            ),
            patch(
                "app.core.metadata.ai.httpx.post",
                return_value=FakeResponse(body_without_usage),
            ),
        ):
            result = parse_with_ai("Another.2022.mkv")
        expect("parse estimated title", result.get("title") if result else None, "Another")
        summary = ai_usage_summary(days=30)
        expect("usage estimated flag", summary["recent"][0]["estimated"], True)
        expect("usage estimated prompt", summary["recent"][0]["prompt_tokens"] > 0, True)
        expect("usage estimated completion", summary["recent"][0]["completion_tokens"] > 0, True)

        # DeepSeek 三档价 + 峰谷倍率。
        deepseek = client.put(
            "/api/settings",
            json={
                "values": {
                    "ai_input_price": 0.15,
                    "ai_cached_input_price": 0.003,
                    "ai_output_price": 0.60,
                    "ai_peak_multiplier": 2,
                    "ai_offpeak_multiplier": 1,
                    "ai_peak_windows": "01:00-04:00,06:00-10:00",
                    "ai_weekend_offpeak": True,
                }
            },
        )
        expect("deepseek settings status", deepseek.status_code, 200)
        expect("deepseek cached price", deepseek.json().get("ai_cached_input_price"), 0.003)

        peak_record = record_ai_usage(
            model="deepseek-flash",
            prompt_tokens=1_000_000,
            cached_tokens=500_000,
            completion_tokens=1_000_000,
            at=datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc),
        )
        expect("peak flag", peak_record["peak"], True)
        expect("peak multiplier", peak_record["multiplier"], 2.0)
        expect("peak cached tokens", peak_record["cached_tokens"], 500_000)
        expect("peak uncached tokens", peak_record["uncached_tokens"], 500_000)
        expect("peak cost", peak_record["cost"], 1.353)

        offpeak_record = record_ai_usage(
            model="deepseek-flash",
            prompt_tokens=1_000_000,
            cached_tokens=500_000,
            completion_tokens=1_000_000,
            at=datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc),
        )
        expect("weekend offpeak flag", offpeak_record["peak"], False)
        expect("offpeak multiplier", offpeak_record["multiplier"], 1.0)
        expect("offpeak cost", offpeak_record["cost"], 0.6765)

        expect(
            "peak time detector",
            is_peak_time(datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc)),
            True,
        )
        expect(
            "weekend detector",
            is_peak_time(datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)),
            False,
        )
        expect(
            "national day holiday offpeak",
            is_peak_time(datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc)),
            False,
        )
        expect(
            "normal weekday peak",
            is_peak_time(datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)),
            True,
        )

        custom_holidays = client.put(
            "/api/settings",
            json={"values": {"ai_cn_holidays": "2026-12-25"}},
        )
        expect("custom holidays saved", custom_holidays.status_code, 200)
        expect(
            "custom holiday offpeak",
            is_peak_time(datetime(2026, 12, 25, 2, 0, tzinfo=timezone.utc)),
            False,
        )
        expect(
            "day before custom holiday peak",
            is_peak_time(datetime(2026, 12, 24, 2, 0, tzinfo=timezone.utc)),
            True,
        )

        summary = ai_usage_summary(days=30)
        expect("summary cached tokens", summary["all_time"]["cached_tokens"], 1_000_080)
        expect("summary prices cached", summary["prices"]["cached"], 0.003)
        expect("summary recent peak badge", summary["recent"][0]["peak"], False)

        # 平台返回 cost：provider 模式直接使用真实费用。
        client.put("/api/settings", json={"values": {"ai_pricing_mode": "provider"}})
        body_with_cost = {
            "choices": [{"message": {"content": '{"title":"Provider"}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.0123},
        }
        with (
            patch("app.core.metadata.ai.ai_available", return_value=True),
            patch(
                "app.core.metadata.ai._ai_config",
                return_value=("https://example.test/v1", "gpt-mock", "sk-test"),
            ),
            patch(
                "app.core.metadata.ai.httpx.post",
                return_value=FakeResponse(body_with_cost),
            ),
        ):
            parse_with_ai("Provider.2025.mkv")
        summary = ai_usage_summary(days=30)
        expect("provider cost source", summary["recent"][0]["cost_source"], "provider")
        expect("provider reported cost", summary["recent"][0]["reported_cost"], 0.0123)
        expect("provider final cost", summary["recent"][0]["cost"], 0.0123)

        # 手动模式即使平台返回 cost，也按手动三档价计算。
        client.put("/api/settings", json={"values": {"ai_pricing_mode": "manual"}})
        with (
            patch("app.core.metadata.ai.ai_available", return_value=True),
            patch(
                "app.core.metadata.ai._ai_config",
                return_value=("https://example.test/v1", "gpt-mock", "sk-test"),
            ),
            patch(
                "app.core.metadata.ai.httpx.post",
                return_value=FakeResponse(body_with_cost),
            ),
        ):
            parse_with_ai("Manual.2025.mkv")
        summary = ai_usage_summary(days=30)
        expect("manual ignores reported cost", summary["recent"][0]["cost"] != 0.0123, True)
        expect("manual cost source", summary["recent"][0]["cost_source"], "manual")
        expect("manual reported kept", summary["recent"][0]["reported_cost"], 0.0123)

        # OpenRouter / LiteLLM 模式使用同步价，不叠加峰谷。
        client.put(
            "/api/settings",
            json={
                "values": {
                    "ai_pricing_mode": "openrouter",
                    "ai_input_price": 0.1,
                    "ai_cached_input_price": 0.01,
                    "ai_output_price": 0.2,
                    "ai_peak_multiplier": 2,
                    "ai_offpeak_multiplier": 1,
                    "ai_peak_windows": "01:00-04:00,06:00-10:00",
                }
            },
        )
        synced = record_ai_usage(
            model="openrouter-model",
            prompt_tokens=1_000_000,
            cached_tokens=500_000,
            completion_tokens=1_000_000,
            at=datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc),
        )
        expect("openrouter cost", synced["cost"], 0.255)
        expect("openrouter no peak", synced["peak"], False)
        expect("openrouter multiplier", synced["multiplier"], 1.0)
        expect("openrouter cost source", synced["cost_source"], "openrouter")

        cleared = client.delete("/api/settings/ai-usage")
        expect("clear usage status", cleared.status_code, 200)
        expect("clear usage empty", client.get("/api/settings/ai-usage").json()["all_time"]["calls"], 0)
        clear_ai_usage()

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
