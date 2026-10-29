from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import VERSION
from ..db import execute, get_setting, query_one, set_setting
from ..core.metadata.ai import (
    AIError,
    DEFAULT_AI_BASE_URL,
    DEFAULT_AI_MODEL,
    test_ai,
)
from ..core.ai_usage import DEFAULT_CN_HOLIDAYS, ai_usage_summary, clear_ai_usage
from ..core.ai_pricing import PricingError, sync_pricing
from ..core.ai_holidays import HolidayError, sync_holidays
from ..core.metadata.tmdb import TMDBClient, TMDBError
from ..core.p115_open import (
    P115OpenClient,
    authorize_with_refresh_token,
    clear_token,
    refresh_now,
    token_status,
)
from ..core.tag_dictionary import get_custom_tags, normalize_tag_dictionary, set_custom_tags
from ..security import decrypt, encrypt
from .deps import require_auth

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(require_auth)])

SECRET_SETTINGS = {"tmdb_api_key", "ai_api_key", "quark_cookie", "p115_cookie"}
ALLOWED = {
    "tmdb_api_key", "tmdb_language", "tmdb_qps",
    "auto_threshold", "review_threshold",
    "min_size_mb", "max_files_per_scan", "allow_iso",
    "auto_scan_enabled", "auto_scan_interval_minutes",
    "ai_enabled", "ai_base_url", "ai_api_key", "ai_model",
    "ai_input_price", "ai_cached_input_price", "ai_output_price",
    "ai_peak_multiplier", "ai_offpeak_multiplier", "ai_peak_windows", "ai_weekend_offpeak",
    "ai_cn_holidays", "ai_pricing_mode",
    "quark_cookie", "quark_qps", "p115_cookie", "p115_qps", "p115_auth_mode",
    "bangumi_enabled", "bangumi_api_base", "mikan_api_base",
    "bangumi_main_url", "bangumi_mirror_url",
    "mikan_main_url", "mikan_mirror_url",
}


class SettingsBody(BaseModel):
    values: dict[str, Any]


class TagDictionaryBody(BaseModel):
    edition: list[str] = []
    release_notes: list[str] = []
    group: list[str] = []


class TmdbTestBody(BaseModel):
    api_key: str = ""


class AiTestBody(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""


class QuarkTestBody(BaseModel):
    cookie: str = ""


class P115TestBody(BaseModel):
    cookie: str = ""


class P115OpenTokenBody(BaseModel):
    refresh_token: str = ""


class AiPricingSyncBody(BaseModel):
    source: str = ""
    model: str = ""


class HolidaySyncBody(BaseModel):
    years: list[int] = []


def masked(key: str) -> bool:
    return bool(get_setting(key, ""))


@router.get("")
def read_settings():
    return {
        "tmdb_api_key_set": masked("tmdb_api_key"),
        "tmdb_language": get_setting("tmdb_language", "zh-CN"),
        "tmdb_qps": get_setting("tmdb_qps", 4),
        "auto_threshold": get_setting("auto_threshold", 0.72),
        "review_threshold": get_setting("review_threshold", 0.5),
        "min_size_mb": get_setting("min_size_mb", 10),
        "max_files_per_scan": get_setting("max_files_per_scan", 3000),
        "allow_iso": get_setting("allow_iso", True),
        "auto_scan_enabled": get_setting("auto_scan_enabled", False),
        "auto_scan_interval_minutes": get_setting("auto_scan_interval_minutes", 15),
        "ai_enabled": get_setting("ai_enabled", False),
        "ai_base_url": get_setting("ai_base_url", DEFAULT_AI_BASE_URL),
        "ai_api_key_set": masked("ai_api_key"),
        "ai_model": get_setting("ai_model", DEFAULT_AI_MODEL),
        "ai_input_price": get_setting("ai_input_price", 0.15),
        "ai_cached_input_price": get_setting("ai_cached_input_price", 0.15),
        "ai_output_price": get_setting("ai_output_price", 0.60),
        "ai_peak_multiplier": get_setting("ai_peak_multiplier", 1.0),
        "ai_offpeak_multiplier": get_setting("ai_offpeak_multiplier", 1.0),
        "ai_peak_windows": get_setting("ai_peak_windows", "01:00-04:00,06:00-10:00"),
        "ai_weekend_offpeak": get_setting("ai_weekend_offpeak", True),
        "ai_cn_holidays": get_setting("ai_cn_holidays", DEFAULT_CN_HOLIDAYS),
        "ai_cn_holidays_updated_at": get_setting("ai_cn_holidays_updated_at", ""),
        "ai_cn_holidays_source": get_setting("ai_cn_holidays_source", ""),
        "ai_pricing_mode": get_setting("ai_pricing_mode", "manual"),
        "quark_cookie_set": masked("quark_cookie"),
        "quark_qps": get_setting("quark_qps", 2),
        "p115_cookie_set": masked("p115_cookie"),
        "p115_qps": get_setting("p115_qps", 1),
        "p115_auth_mode": get_setting("p115_auth_mode", "cookie"),
        **{f"p115_open_{key}": value for key, value in token_status().items()},
        "bangumi_enabled": get_setting("bangumi_enabled", False),
        "bangumi_api_base": get_setting("bangumi_api_base", "https://api.bgm.tv"),
        "mikan_api_base": get_setting("mikan_api_base", "https://mikanani.me"),
        "bangumi_main_url": get_setting("bangumi_main_url", "https://api.bgm.tv"),
        "bangumi_mirror_url": get_setting("bangumi_mirror_url", "https://bgm.tv"),
        "mikan_main_url": get_setting("mikan_main_url", "https://mikanani.me"),
        "mikan_mirror_url": get_setting("mikan_mirror_url", "https://mikanime.tv"),
        "version": VERSION,
    }


@router.put("")
def update_settings(body: SettingsBody):
    values = body.values or {}
    for key, value in values.items():
        if key not in ALLOWED:
            continue
        if key in SECRET_SETTINGS:
            if value:
                set_setting(key, encrypt(str(value)))
            continue
        if key == "tmdb_qps":
            value = max(0.5, min(float(value or 4), 20))
        if key == "quark_qps":
            value = max(0.2, min(float(value or 2), 20))
        if key == "p115_qps":
            value = max(0.2, min(float(value or 1), 10))
        if key == "p115_auth_mode":
            value = value if value in {"cookie", "open"} else "cookie"
        if key in {"auto_threshold", "review_threshold"}:
            value = max(0.05, min(float(value or 0.5), 0.99))
        if key in {"ai_input_price", "ai_cached_input_price", "ai_output_price"}:
            value = max(0.0, min(float(value or 0), 10000.0))
        if key in {"ai_peak_multiplier", "ai_offpeak_multiplier"}:
            value = max(0.0, min(float(value if value is not None else 1), 100.0))
        if key == "ai_peak_windows":
            value = str(value or "")[:200]
        if key == "ai_cn_holidays":
            value = str(value or "")[:4000]
        if key == "ai_pricing_mode":
            value = value if value in {"manual", "provider", "openrouter", "litellm"} else "manual"
        if key == "ai_weekend_offpeak":
            value = bool(value)
        if key == "bangumi_api_base":
            value = str(value or "").strip().rstrip("/")
            if not value.startswith(("http://", "https://")):
                value = "https://api.bgm.tv"
            value = value[:200]
        if key == "mikan_api_base":
            value = str(value or "").strip().rstrip("/")
            if not value.startswith(("http://", "https://")):
                value = "https://mikanani.me"
            value = value[:200]
        if key in {"bangumi_main_url", "bangumi_mirror_url", "mikan_main_url", "mikan_mirror_url"}:
            value = str(value or "").strip().rstrip("/")
            if not value.startswith(("http://", "https://")):
                defaults = {
                    "bangumi_main_url": "https://api.bgm.tv",
                    "bangumi_mirror_url": "https://bgm.tv",
                    "mikan_main_url": "https://mikanani.me",
                    "mikan_mirror_url": "https://mikanime.tv",
                }
                value = defaults.get(key, "https://api.bgm.tv")
            value = value[:200]
        if key == "min_size_mb":
            value = max(0, float(value or 0))
        if key == "max_files_per_scan":
            value = max(1, min(int(value or 3000), 20000))
        if key == "auto_scan_interval_minutes":
            value = max(5, min(int(value or 15), 1440))
        if key in {"allow_iso", "ai_enabled", "bangumi_enabled", "auto_scan_enabled"}:
            value = bool(value)
        set_setting(key, value)
    return read_settings()


@router.post("/test-tmdb")
def test_tmdb(body: TmdbTestBody):
    api_key = body.api_key or decrypt(get_setting("tmdb_api_key", "") or "")
    if not api_key:
        raise HTTPException(status_code=400, detail="请先填写 TMDb API Key")
    client = TMDBClient(api_key=api_key, language=get_setting("tmdb_language", "zh-CN"))
    try:
        results = client.search("千与千寻", "movie")
    except TMDBError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "sample": results[0]["title"] if results else ""}


@router.post("/test-quark")
def test_quark_endpoint(body: QuarkTestBody | None = None):
    from ..core.quark import QuarkClient, QuarkError

    payload = body or QuarkTestBody()
    cookie = payload.cookie or decrypt(get_setting("quark_cookie", "") or "")
    if not cookie:
        raise HTTPException(status_code=400, detail="请先填写夸克 Cookie")
    try:
        qps = float(get_setting("quark_qps", 2) or 2)
    except (TypeError, ValueError):
        qps = 2.0
    try:
        with QuarkClient(cookie, qps=max(0.2, min(qps, 20.0))) as client:
            return client.test()
    except QuarkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/test-115")
def test_115_endpoint(body: P115TestBody | None = None):
    from ..core.p115 import P115Client, P115Error

    payload = body or P115TestBody()
    try:
        qps = float(get_setting("p115_qps", 1) or 1)
    except (TypeError, ValueError):
        qps = 1.0
    qps = max(0.2, min(qps, 10.0))
    try:
        if not payload.cookie and str(get_setting("p115_auth_mode", "cookie")) == "open":
            with P115OpenClient(qps=qps) as client:
                return client.test()
        cookie = payload.cookie or decrypt(get_setting("p115_cookie", "") or "")
        if not cookie:
            raise HTTPException(status_code=400, detail="请先填写 115 Cookie")
        with P115Client(cookie, qps=qps) as client:
            return client.test()
    except P115Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/115-open/status")
def p115_open_status():
    return token_status()


@router.post("/115-open/token")
def save_p115_open_token(body: P115OpenTokenBody):
    try:
        data = authorize_with_refresh_token(body.refresh_token)
    except P115Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    set_setting("p115_auth_mode", "open")
    return {"ok": True, "nickname": data.get("nickname", ""), **token_status()}


@router.post("/115-open/refresh")
def refresh_p115_open_token():
    try:
        return {"ok": True, **refresh_now()}
    except P115Error as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/115-open/token")
def delete_p115_open_token():
    clear_token()
    if str(get_setting("p115_auth_mode", "cookie")) == "open":
        set_setting("p115_auth_mode", "cookie")
    return {"ok": True, **token_status()}


@router.post("/test-ai")
def test_ai_endpoint(body: AiTestBody | None = None):
    payload = body or AiTestBody()
    try:
        return test_ai(base_url=payload.base_url, model=payload.model, api_key=payload.api_key)
    except AIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ai-usage")
def read_ai_usage(days: int = 30):
    return ai_usage_summary(days=days)


@router.post("/ai-pricing/sync")
def sync_ai_pricing(body: AiPricingSyncBody | None = None):
    payload = body or AiPricingSyncBody()
    source = payload.source or str(get_setting("ai_pricing_mode", "manual") or "manual")
    model = payload.model or str(get_setting("ai_model", "") or DEFAULT_AI_MODEL)
    if source not in {"openrouter", "litellm"}:
        raise HTTPException(status_code=400, detail="请选择 OpenRouter 或 LiteLLM 价格源")
    try:
        return sync_pricing(source, model)
    except PricingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai-holidays/sync")
def sync_ai_holidays(body: HolidaySyncBody | None = None):
    payload = body or HolidaySyncBody()
    try:
        return sync_holidays(payload.years or None)
    except HolidayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/ai-usage")
def clear_ai_usage_endpoint():
    clear_ai_usage()
    return {"ok": True}


@router.get("/tags")
def read_tag_dictionary():
    """影视版本 / 发布标签 / 发布组的自定义词条。"""
    return get_custom_tags()


@router.put("/tags")
def update_tag_dictionary(body: TagDictionaryBody):
    values = normalize_tag_dictionary(body.model_dump())
    set_setting("custom_tag_dictionary", values)
    return set_custom_tags(values)


@router.delete("/cache")
def clear_cache():
    execute("DELETE FROM tmdb_cache")
    return {"ok": True}


@router.get("/system")
def system_info():
    import platform
    import sys
    from ..config import settings as app_settings

    item_count = query_one("SELECT COUNT(*) AS c FROM items")
    return {
        "version": VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "data_dir": str(app_settings.data_dir),
        "items": item_count["c"] if item_count else 0,
    }
