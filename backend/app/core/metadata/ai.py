from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ...db import get_setting
from ..ai_usage import estimate_tokens, record_ai_usage


class AIError(RuntimeError):
    pass


class WebSearchUnavailable(AIError):
    """当前模型 / 网关不支持联网搜索，调用方应回退到普通对话。"""


DEFAULT_AI_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_AI_MODEL = "deepseek-flash"
WEB_SEARCH_MODES = {"off", "auto", "on"}


SYSTEM_PROMPT = """你是影视库文件识别助手。输入一个视频文件名和所在目录，输出 JSON。
只输出 JSON，不要解释。字段：
{
  "title": "作品主标题（中文优先；没有中文就用原文）",
  "original_title": "原文标题（可为空）",
  "aliases": ["可用于搜索的别名 / 英文名 / 日文名 / 罗马音 / 常见翻译，最多 5 个"],
  "year": 年份数字或 null,
  "media_type": "movie" 或 "tv",
  "season": 季号数字或 null,
  "episode": 集号数字或 null,
  "confidence": 0.0 到 1.0 的数字,
  "reason": "一句话说明如何从文件名 / 目录判断"
}
规则：
- 优先保留作品真实标题，去掉画质、来源、字幕组、站点广告等噪声。
- 字幕组文件名常见格式是 [组名][标题][集数][标签]；第一个括号通常是组名，不是标题。
- 文件名只是集号（如 01.mkv）时，根据目录名推断作品名。
- 无法确定年份 / 季号 / 集号时返回 null，不要猜。
"""

WEB_SEARCH_ADDENDUM = """
你可以使用联网搜索确认作品。请先搜索，再根据查到的真实资料判断；如果搜索结果互相矛盾或找不到，降低 confidence。
最终只输出一个 JSON 代码块，字段与上面相同，并额外增加：
  "sources": ["参考来源链接，最多 5 个"]
JSON 代码块之外不要输出其他内容。
"""

PICK_PROMPT = """你是影视库文件识别助手。下面是文件信息和 TMDb 的真实搜索结果。
请从候选中选出最匹配的一个；没有合适的就返回 index = -1，不要硬选。
只输出 JSON：
{
  "index": 候选序号（从 0 开始）或 -1,
  "confidence": 0.0 到 1.0,
  "aliases": ["可用于以后搜索的别名 / 罗马音 / 英文名，最多 5 个"],
  "reason": "一句话说明"
}
"""

# 网关不支持联网搜索时，本进程内不再重复尝试。
_WEB_SEARCH_DISABLED: set[tuple[str, str]] = set()


def ai_available() -> bool:
    from ...security import decrypt

    return bool(get_setting("ai_enabled", False) and decrypt(get_setting("ai_api_key", "") or ""))


def _ai_config() -> tuple[str, str, str]:
    from ...security import decrypt

    base_url = (get_setting("ai_base_url", "") or DEFAULT_AI_BASE_URL).rstrip("/")
    model = (get_setting("ai_model", "") or DEFAULT_AI_MODEL).strip()
    api_key = decrypt(get_setting("ai_api_key", "") or "")
    return base_url, model, api_key


def web_search_mode() -> str:
    value = str(get_setting("ai_web_search", "auto") or "auto").strip().lower()
    return value if value in WEB_SEARCH_MODES else "auto"


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里取 JSON；兼容 ```json 代码块和前后解释文字。"""
    if not text:
        return None
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for block in reversed(fenced):
        try:
            data = json.loads(block)
            if isinstance(data, dict):
                return data
        except ValueError:
            continue
    start = text.find("{")
    end = text.rfind("}")
    while start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        end = text.rfind("}", start, end)
    return None


def _normalize_hint(data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(data, dict) or not data.get("title"):
        return None
    year = data.get("year")
    season = data.get("season")
    episode = data.get("episode")
    raw_aliases = data.get("aliases")
    aliases: list[str] = []
    if isinstance(raw_aliases, list):
        for item in raw_aliases:
            value = str(item or "").strip()
            if value and value not in aliases:
                aliases.append(value)
    elif isinstance(raw_aliases, str):
        aliases = [part.strip() for part in raw_aliases.split(",") if part.strip()]
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    sources = data.get("sources")
    if not isinstance(sources, list):
        sources = []
    return {
        "title": str(data.get("title") or "").strip(),
        "title_alt": str(data.get("original_title") or "").strip(),
        "aliases": aliases[:5],
        "year": int(year) if isinstance(year, (int, str)) and str(year).isdigit() else None,
        "media_type": "tv" if data.get("media_type") == "tv" else "movie",
        "season": int(season) if isinstance(season, (int, str)) and str(season).isdigit() else None,
        "episode": int(episode) if isinstance(episode, (int, str)) and str(episode).isdigit() else None,
        "confidence": max(0.0, min(confidence, 1.0)),
        "reason": str(data.get("reason") or "").strip()[:300],
        "sources": [str(value).strip()[:300] for value in sources if str(value).strip()][:5],
    }


def _record_usage(
    body: dict[str, Any],
    *,
    model: str,
    file_name: str,
    context: str,
    fallback_prompt: str,
    fallback_output: str,
) -> None:
    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion_tokens = usage.get(
        "completion_tokens", usage.get("output_tokens")
    )
    cache_hit = usage.get("prompt_cache_hit_tokens")
    cache_miss = usage.get("prompt_cache_miss_tokens")
    cached_tokens = usage.get("cached_tokens")
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
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
        prompt_tokens = estimate_tokens(fallback_prompt)
        completion_tokens = estimate_tokens(fallback_output)
        cached_tokens = 0
    try:
        record_ai_usage(
            model=model,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            cached_tokens=int(cached_tokens or 0),
            reported_cost=reported_cost,
            context=context,
            file_name=file_name,
            estimated=estimated,
        )
    except Exception:
        # 统计失败不应该影响识别结果。
        pass


def _chat_hint(
    base_url: str,
    model: str,
    api_key: str,
    file_name: str,
    folder_path: str,
    extra_context: str = "",
) -> dict[str, Any] | None:
    user_content = json.dumps(
        {"file_name": file_name, "folder": folder_path, "context": extra_context},
        ensure_ascii=False,
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
            headers=_headers(api_key),
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
    _record_usage(
        body,
        model=model,
        file_name=file_name,
        context="cross_validate",
        fallback_prompt=SYSTEM_PROMPT + "\n" + user_content,
        fallback_output=content,
    )
    hint = _normalize_hint(data)
    if hint:
        hint["source"] = "chat"
    return hint


def _responses_web_hint(
    base_url: str,
    model: str,
    api_key: str,
    file_name: str,
    folder_path: str,
    extra_context: str = "",
) -> dict[str, Any] | None:
    user_content = json.dumps(
        {"file_name": file_name, "folder": folder_path, "context": extra_context},
        ensure_ascii=False,
    )
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT + WEB_SEARCH_ADDENDUM},
            {"role": "user", "content": user_content},
        ],
        "tools": [{"type": "web_search"}],
    }
    try:
        response = httpx.post(
            f"{base_url}/responses",
            headers=_headers(api_key),
            json=payload,
            timeout=120,
        )
    except httpx.HTTPError as exc:
        raise AIError(f"联网搜索请求失败: {exc}") from exc
    if response.status_code in (404, 405):
        raise WebSearchUnavailable("网关没有 /responses 接口")
    if response.status_code >= 400:
        message = response.text[:200]
        if "web_search" in message or "tool" in message.lower():
            raise WebSearchUnavailable(message)
        raise AIError(f"联网搜索返回 HTTP {response.status_code}: {message}")
    try:
        body = response.json()
    except ValueError as exc:
        raise AIError("联网搜索响应无法解析") from exc

    texts: list[str] = []
    sources: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            for source in action.get("sources") or []:
                if isinstance(source, dict) and source.get("url"):
                    sources.append(str(source["url"]))
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"}:
                texts.append(str(part.get("text") or ""))
            for annotation in part.get("annotations") or []:
                if isinstance(annotation, dict) and annotation.get("url"):
                    sources.append(str(annotation["url"]))

    content = "\n".join(texts)
    data = _extract_json(content)
    if data is None:
        raise AIError("联网搜索没有返回可解析的 JSON")
    _record_usage(
        body,
        model=model,
        file_name=file_name,
        context="web_search",
        fallback_prompt=SYSTEM_PROMPT + WEB_SEARCH_ADDENDUM + "\n" + user_content,
        fallback_output=content,
    )
    hint = _normalize_hint(data)
    if not hint:
        return None
    hint["source"] = "web_search"
    if not hint.get("sources"):
        hint["sources"] = list(dict.fromkeys(sources))[:5]
    return hint


def parse_with_ai(
    file_name: str,
    folder_path: str = "",
    *,
    web_search: bool | None = None,
    extra_context: str = "",
) -> dict[str, Any] | None:
    """识别文件名；优先联网搜索，失败自动回退到普通对话。"""
    if not ai_available():
        return None
    base_url, model, api_key = _ai_config()
    if web_search is None:
        web_search = web_search_mode() != "off"
    key = (base_url, model)
    errors: list[str] = []

    if web_search and key not in _WEB_SEARCH_DISABLED:
        try:
            hint = _responses_web_hint(
                base_url, model, api_key, file_name, folder_path, extra_context
            )
            if hint:
                return hint
            errors.append("联网搜索未返回可用结果")
        except WebSearchUnavailable as exc:
            _WEB_SEARCH_DISABLED.add(key)
            errors.append(f"联网搜索不可用: {exc}")
        except AIError as exc:
            errors.append(str(exc))

    try:
        hint = _chat_hint(
            base_url, model, api_key, file_name, folder_path, extra_context
        )
    except AIError as exc:
        errors.append(str(exc))
        hint = None
    if hint:
        if errors:
            hint["ai_error"] = "; ".join(errors)[:400]
        return hint
    if errors:
        raise AIError("; ".join(errors)[:400])
    return None


def chat_json(
    system_prompt: str,
    payload: dict[str, Any],
    *,
    context: str,
    file_name: str = "",
    timeout: float = 45,
) -> dict[str, Any] | None:
    """通用 JSON 对话，供候选挑选之外的场景（如 AI 选目录）复用。"""
    if not ai_available():
        return None
    base_url, model, api_key = _ai_config()
    user_content = json.dumps(payload, ensure_ascii=False)
    request = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers=_headers(api_key),
            json=request,
            timeout=timeout,
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
    _record_usage(
        body,
        model=model,
        file_name=file_name,
        context=context,
        fallback_prompt=system_prompt + "\n" + user_content,
        fallback_output=content,
    )
    return data if isinstance(data, dict) else None


def pick_candidate(
    file_name: str,
    folder_path: str,
    candidates: list[dict[str, Any]],
    *,
    extra_context: str = "",
) -> dict[str, Any] | None:
    """把 TMDb 的真实候选交给模型挑选，返回 index / confidence / reason。"""
    if not ai_available() or not candidates:
        return None
    base_url, model, api_key = _ai_config()
    compact = []
    for index, candidate in enumerate(candidates[:10]):
        compact.append(
            {
                "index": index,
                "title": candidate.get("title") or "",
                "original_title": candidate.get("original_title") or "",
                "year": candidate.get("year"),
                "media_type": candidate.get("media_type") or "",
                "overview": str(candidate.get("overview") or "")[:200],
            }
        )
    user_content = json.dumps(
        {
            "file_name": file_name,
            "folder": folder_path,
            "context": extra_context,
            "candidates": compact,
        },
        ensure_ascii=False,
    )
    payload = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PICK_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers=_headers(api_key),
            json=payload,
            timeout=45,
        )
    except httpx.HTTPError as exc:
        raise AIError(f"候选挑选请求失败: {exc}") from exc
    if response.status_code >= 400:
        raise AIError(f"候选挑选返回 HTTP {response.status_code}")
    try:
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        data = json.loads(content)
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        raise AIError("候选挑选返回内容无法解析") from exc
    _record_usage(
        body,
        model=model,
        file_name=file_name,
        context="pick_candidate",
        fallback_prompt=PICK_PROMPT + "\n" + user_content,
        fallback_output=content,
    )
    try:
        index = int(data.get("index"))
    except (TypeError, ValueError):
        return None
    if index < 0 or index >= len(compact):
        return {"index": -1, "confidence": 0.0, "reason": str(data.get("reason") or "")[:300]}
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    aliases = data.get("aliases")
    if not isinstance(aliases, list):
        aliases = []
    return {
        "index": index,
        "confidence": max(0.0, min(confidence, 1.0)),
        "reason": str(data.get("reason") or "").strip()[:300],
        "aliases": [str(value).strip() for value in aliases if str(value).strip()][:5],
    }


def test_ai(base_url: str = "", model: str = "", api_key: str = "") -> dict[str, Any]:
    """连接测试：优先使用传入的参数（页面上刚填写的），缺省回退到已保存的配置。"""
    from ...security import decrypt

    base_url = (base_url or get_setting("ai_base_url", "") or DEFAULT_AI_BASE_URL).rstrip("/")
    model = model or get_setting("ai_model", "") or DEFAULT_AI_MODEL
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
