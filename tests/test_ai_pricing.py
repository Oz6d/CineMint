"""AI 价格同步适配器的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_ai_pricing.py
使用临时数据目录，不会访问真实价格接口。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DATA = tempfile.mkdtemp(prefix="cinemint-ai-pricing-")
os.environ["CINEMINT_DATA_DIR"] = TEMP_DATA
os.environ["CINEMINT_DEMO"] = "false"
os.environ["CINEMINT_ADMIN_PASSWORD"] = "cinemint"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.ai_pricing import (  # noqa: E402
    PricingError,
    clear_pricing_cache,
    sync_pricing,
)
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

    openrouter_payload = {
        "data": [
            {
                "id": "deepseek/deepseek-chat",
                "name": "DeepSeek Chat",
                "pricing": {
                    "prompt": "0.00000015",
                    "completion": "0.0000006",
                    "input_cache_read": "0.000000003",
                },
            }
        ]
    }
    litellm_payload = {
        "deepseek/deepseek-chat": {
            "input_cost_per_token": 0.00000015,
            "output_cost_per_token": 0.0000006,
            "cache_read_input_token_cost": 0.000000003,
        }
    }

    clear_pricing_cache()
    with patch(
        "app.core.ai_pricing.httpx.get",
        return_value=FakeResponse(openrouter_payload),
    ) as mocked:
        result = sync_pricing("openrouter", "deepseek/deepseek-chat")
        expect("openrouter input", result["input"], 0.15)
        expect("openrouter cached", result["cached"], 0.003)
        expect("openrouter output", result["output"], 0.6)
        expect("openrouter source", result["source"], "openrouter")
        expect("openrouter no peak", result["peak_multiplier"], 1.0)
        expect("openrouter note", "OpenRouter" in result["note"], True)
        sync_pricing("openrouter", "deepseek/deepseek-chat")
        expect("openrouter cached request", mocked.call_count, 1)

    clear_pricing_cache()
    with patch(
        "app.core.ai_pricing.httpx.get",
        return_value=FakeResponse(litellm_payload),
    ):
        result = sync_pricing("litellm", "deepseek-chat")
        expect("litellm input", result["input"], 0.15)
        expect("litellm cached", result["cached"], 0.003)
        expect("litellm output", result["output"], 0.6)
        expect("litellm source", result["source"], "litellm")

    clear_pricing_cache()
    with patch(
        "app.core.ai_pricing.httpx.get",
        return_value=FakeResponse({"data": []}),
    ):
        try:
            sync_pricing("openrouter", "missing-model")
            unknown = None
        except PricingError as exc:
            unknown = str(exc)
        expect("unknown model error", "没有找到模型" in (unknown or ""), True)

    try:
        sync_pricing("manual", "x")
        invalid = None
    except PricingError as exc:
        invalid = str(exc)
    expect("invalid source error", "不支持的价格源" in (invalid or ""), True)

    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"password": "cinemint"})
        expect("login", login.status_code, 200)
        clear_pricing_cache()
        with patch(
            "app.core.ai_pricing.httpx.get",
            return_value=FakeResponse(openrouter_payload),
        ):
            response = client.post(
                "/api/settings/ai-pricing/sync",
                json={"source": "openrouter", "model": "deepseek/deepseek-chat"},
            )
        expect("sync api status", response.status_code, 200)
        expect("sync api input", response.json().get("input"), 0.15)
        expect("sync api output", response.json().get("output"), 0.6)

        invalid_api = client.post(
            "/api/settings/ai-pricing/sync",
            json={"source": "manual", "model": "x"},
        )
        expect("sync api invalid source", invalid_api.status_code, 400)

    clear_pricing_cache()
    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
