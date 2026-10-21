from __future__ import annotations

import json
from typing import Any

import httpx

from ...db import get_setting
from ..ai_usage import estimate_tokens, record_ai_usage


class AIError(RuntimeError):
    pass


SYSTEM_PROMPT = """你是影视库文件识别助手。输入一个视频文件名和所在目录，输出 JSON。
只输出 JSON，不要解释。字段：
{
  "title": "作品主标题（中文优先；没有中文就用原文）",
  "original_title": "原文标题（可为空）",
  "year": 年份数字或 null,
  "media_type": "movie" 或 "tv",
  "season": 季号数字或 null,
  "episode": 集号数字或 null
}
规则：
- 优先保留作品真实标题，去掉画质、来源、字幕组、站点广告等噪声。
- 文件名只是集号（如 01.mkv）时，根据目录名推断作品名。
- 无法确定年份时返回 null，不要猜。
"""


def ai_available() -> bool:
    from ...security import decrypt

    return bool(get_setting("ai_enabled", False) and decrypt(get_setting("ai_api_key", "") or ""))


def _ai_config() -> tuple[str, str, str]:
    from ...security import decrypt

    base_url = (get_setting("ai_base_url", "") or "https://api.openai.com/v1").rstrip("/")
    model = get_setting("ai_model", "") or "gpt-4o-mini"
    api_key = decrypt(get_setting("ai_api_key", "") or "")
    return base_url, model, api_key


def parse_with_ai(file_name: str, folder_path: str = "") -> dict[str, Any] | None:
    if not ai_available():
        return None
    base_url, model, api_key = _ai_config()
    user_content = json.dumps(
        {"file_name": file_name, "folder": folder_path}, ensure_ascii=False
    )
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
    except httpx.HTTPError as exc:
        raise AIError(f"AI 请求失败: {exc}") from exc
    if response.status_code >= 400:
        raise AIError(f"AI 返回 HTTP {response.status_code}")
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        data = json.loads(content)
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        raise AIError("AI 返回内容无法解析") from exc

    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    cache_hit = usage.get("prompt_cache_hit_tokens")
    cache_miss = usage.get("prompt_cache_miss_tokens")
    cached_tokens = usage.get("cached_tokens")
    details = usage.get("prompt_tokens_details")
    if cached_tokens is None and isinstance(details, dict):
        cached_tokens = details.get("cached_tokens")
    if cached_tokens is None:
        cached_tokens = cache_hit
    if prompt_tokens is None and cache_hit is not None and cache_miss is not None:
        prompt_tokens = int(cache_hit) + int(cache_miss)
    reported_cost = None
    for key in ("total_cost", "cost"):
        value = usage.get(key)
        if value is not None:
            reported_cost = value
            break
    if reported_cost is None and isinstance(body, dict):
        reported_cost = body.get("cost")
    estimated = not (
        isinstance(prompt_tokens, (int, float))
        and isinstance(completion_tokens, (int, float))
    )
    if estimated:
        prompt_tokens = estimate_tokens(SYSTEM_PROMPT + "\n" + user_content)
        completion_tokens = estimate_tokens(content)
        cached_tokens = 0
    try:
        record_ai_usage(
            model=model,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            cached_tokens=int(cached_tokens or 0),
            reported_cost=reported_cost,
            context="cross_validate",
            file_name=file_name,
            estimated=estimated,
        )
    except Exception:
        # 统计失败不应该影响识别结果。
        pass

    if not isinstance(data, dict) or not data.get("title"):
        return None
    year = data.get("year")
    season = data.get("season")
    episode = data.get("episode")
    return {
        "title": str(data.get("title") or "").strip(),
        "title_alt": str(data.get("original_title") or "").strip(),
        "year": int(year) if isinstance(year, (int, str)) and str(year).isdigit() else None,
        "media_type": "tv" if data.get("media_type") == "tv" else "movie",
        "season": int(season) if isinstance(season, (int, str)) and str(season).isdigit() else None,
        "episode": int(episode) if isinstance(episode, (int, str)) and str(episode).isdigit() else None,
    }


def test_ai(base_url: str = "", model: str = "", api_key: str = "") -> dict[str, Any]:
    """连接测试：优先使用传入的参数（页面上刚填写的），缺省回退到已保存的配置。"""
    from ...security import decrypt

    base_url = (base_url or get_setting("ai_base_url", "") or "https://api.openai.com/v1").rstrip("/")
    model = model or get_setting("ai_model", "") or "gpt-4o-mini"
    key = api_key or decrypt(get_setting("ai_api_key", "") or "")
    if not key:
        raise AIError("未填写 API Key：请在下方输入框中粘贴 API Key 后再点测试")
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise AIError(f"AI 请求失败: {exc}") from exc
    if response.status_code >= 400:
        raise AIError(f"AI 返回 HTTP {response.status_code}: {response.text[:200]}")
    return {"ok": True, "model": model}
