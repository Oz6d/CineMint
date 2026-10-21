"""AI 价格同步适配器。

目前支持：
- OpenRouter `/api/v1/models` 的实时模型价格；
- LiteLLM 公开价格库 JSON。

DeepSeek 官方没有公开的价格查询 API，仍使用设置里的三档价 + 峰谷规则。
同步只返回建议价格，用户确认保存后才生效。
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
LITELLM_MODELS_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
CACHE_TTL = 3600.0


class PricingError(RuntimeError):
    pass


_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def clear_pricing_cache() -> None:
    with _lock:
        _cache.clear()


def _fetch_json(url: str) -> Any:
    now = time.monotonic()
    with _lock:
        hit = _cache.get(url)
        if hit and hit[0] > now:
            return hit[1]
    try:
        response = httpx.get(
            url,
            timeout=25,
            headers={"User-Agent": "CineFlow/0.1 (+pricing-sync)"},
        )
    except httpx.HTTPError as exc:
        raise PricingError(f"价格源请求失败: {exc}") from exc
    if response.status_code >= 400:
        raise PricingError(f"价格源返回 HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise PricingError("价格源返回内容不是 JSON") from exc
    with _lock:
        _cache[url] = (now + CACHE_TTL, data)
    return data


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _per_million(value: Any) -> float | None:
    number = _number(value)
    return None if number is None else number * 1_000_000


def _openrouter(model: str) -> dict[str, Any]:
    data = _fetch_json(OPENROUTER_MODELS_URL)
    models = data.get("data") if isinstance(data, dict) else data
    if not isinstance(models, list):
        raise PricingError("OpenRouter 返回格式无法识别")
    model = (model or "").strip()
    if not model:
        raise PricingError("请先填写模型名")
    exact = [item for item in models if item.get("id") == model]
    suffix = [
        item
        for item in models
        if str(item.get("id") or "").split("/")[-1] == model
    ]
    fuzzy = [
        item
        for item in models
        if model.lower() in str(item.get("id") or "").lower()
    ]
    candidates = exact or suffix or fuzzy
    if not candidates:
        raise PricingError(f"OpenRouter 没有找到模型: {model}")
    item = candidates[0]
    pricing = item.get("pricing") or {}
    input_price = _per_million(pricing.get("prompt"))
    output_price = _per_million(pricing.get("completion"))
    if input_price is None or output_price is None:
        raise PricingError("OpenRouter 价格字段缺失")
    cached = _per_million(
        pricing.get("input_cache_read")
        or pricing.get("cache_read")
        or pricing.get("cached_input")
    )
    return {
        "source": "openrouter",
        "model": item.get("id") or model,
        "input": input_price,
        "cached": input_price if cached is None else cached,
        "output": output_price,
        "peak_multiplier": 1.0,
        "offpeak_multiplier": 1.0,
        "peak_windows": "",
        "weekend_offpeak": False,
        "note": "OpenRouter 实时模型价格；峰谷规则不适用",
    }


def _litellm(model: str) -> dict[str, Any]:
    data = _fetch_json(LITELLM_MODELS_URL)
    if not isinstance(data, dict):
        raise PricingError("LiteLLM 价格库格式无法识别")
    model = (model or "").strip()
    if not model:
        raise PricingError("请先填写模型名")
    keys = list(data.keys())
    exact = [key for key in keys if key == model]
    suffix = [
        key
        for key in keys
        if str(key).split("/")[-1] == model
    ]
    fuzzy = [
        key
        for key in keys
        if model.lower() in str(key).lower()
    ]
    candidates = exact or suffix or fuzzy
    if not candidates:
        raise PricingError(f"LiteLLM 价格库没有找到模型: {model}")
    key = candidates[0]
    entry = data.get(key) or {}
    input_price = _per_million(entry.get("input_cost_per_token"))
    output_price = _per_million(entry.get("output_cost_per_token"))
    if input_price is None or output_price is None:
        raise PricingError("LiteLLM 价格字段缺失")
    cached = _per_million(
        entry.get("cache_read_input_token_cost")
        or entry.get("cache_hit_cost_per_token")
    )
    return {
        "source": "litellm",
        "model": key,
        "input": input_price,
        "cached": input_price if cached is None else cached,
        "output": output_price,
        "peak_multiplier": 1.0,
        "offpeak_multiplier": 1.0,
        "peak_windows": "",
        "weekend_offpeak": False,
        "note": "LiteLLM 价格库；峰谷规则不适用",
    }


def sync_pricing(source: str, model: str) -> dict[str, Any]:
    if source == "openrouter":
        return _openrouter(model)
    if source == "litellm":
        return _litellm(model)
    raise PricingError("不支持的价格源，请选择 OpenRouter 或 LiteLLM")
