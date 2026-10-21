from __future__ import annotations

import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..config import settings
from ..core.metadata.tmdb import IMAGE_BASE
from .deps import require_auth

router = APIRouter(prefix="/api/images", tags=["images"])

ALLOWED_SIZES = {
    "w45",
    "w92",
    "w154",
    "w185",
    "w200",
    "w300",
    "w342",
    "w500",
    "w780",
    "w1280",
    "original",
}
IMAGE_PATH = re.compile(r"^/[A-Za-z0-9._-]{1,160}$")
CACHE_DIR = settings.data_dir / "cache" / "images"


@router.get("")
def proxy_image(
    path: str = Query(..., max_length=200),
    size: str = Query("w500"),
    _: bool = Depends(require_auth),
):
    if not IMAGE_PATH.match(path) or ".." in path:
        raise HTTPException(status_code=400, detail="图片路径不合法")
    if size not in ALLOWED_SIZES:
        size = "w500"

    cache_file = CACHE_DIR / size / path.lstrip("/")
    if cache_file.is_file():
        return _response(cache_file.read_bytes())

    try:
        upstream = httpx.get(
            f"{IMAGE_BASE}/{size}{path}",
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"图片获取失败：{exc}") from exc
    if upstream.status_code != 200 or not upstream.content:
        raise HTTPException(status_code=404, detail="图片不存在")
    if len(upstream.content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=502, detail="图片过大")

    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(upstream.content)
    except OSError:
        pass

    content_type = upstream.headers.get("content-type") or "image/jpeg"
    return _response(upstream.content, content_type)


def _response(data: bytes, content_type: str = "image/jpeg") -> Response:
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )
