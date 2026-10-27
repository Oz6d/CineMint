from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.notifications import (
    CHANNEL_CATALOG,
    EVENTS,
    NotificationError,
    create_channel,
    delete_channel,
    get_channel,
    list_channels,
    normalize_events,
    test_channel,
    test_config,
    update_channel,
)
from ..db import query_all
from .deps import require_auth

router = APIRouter(
    prefix="/api/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_auth)],
)


class ChannelBody(BaseModel):
    name: str = ""
    type: str
    config: dict[str, Any] = {}
    events: list[str] = []
    enabled: bool = True


class ChannelUpdateBody(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    events: list[str] | None = None
    enabled: bool | None = None


class TestConfigBody(BaseModel):
    type: str
    config: dict[str, Any] = {}


@router.get("/catalog")
def read_catalog():
    return {"events": EVENTS, "channels": CHANNEL_CATALOG}


@router.get("")
def read_channels():
    return {"channels": list_channels()}


@router.post("")
def add_channel(body: ChannelBody):
    try:
        return create_channel(body.model_dump())
    except NotificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{channel_id}")
def edit_channel(channel_id: int, body: ChannelUpdateBody):
    values = body.model_dump(exclude_unset=True)
    if "events" in values:
        values["events"] = normalize_events(values["events"])
    try:
        channel = update_channel(channel_id, values)
    except NotificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    return channel


@router.delete("/{channel_id}")
def remove_channel(channel_id: int):
    if not delete_channel(channel_id):
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    return {"ok": True}


@router.get("/logs")
def read_logs(channel_id: int = 0, limit: int = 100):
    limit = max(1, min(int(limit or 100), 500))
    if channel_id:
        rows = query_all(
            "SELECT l.*, c.name AS channel_name, c.type AS channel_type "
            "FROM notification_logs l LEFT JOIN notification_channels c ON c.id=l.channel_id "
            "WHERE l.channel_id=? ORDER BY l.id DESC LIMIT ?",
            (channel_id, limit),
        )
    else:
        rows = query_all(
            "SELECT l.*, c.name AS channel_name, c.type AS channel_type "
            "FROM notification_logs l LEFT JOIN notification_channels c ON c.id=l.channel_id "
            "ORDER BY l.id DESC LIMIT ?",
            (limit,),
        )
    return {"logs": rows}


@router.post("/test")
def test_unsaved(body: TestConfigBody):
    try:
        return test_config(body.type, body.config)
    except NotificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{channel_id}/test")
def test_saved(channel_id: int):
    if not get_channel(channel_id):
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    try:
        return test_channel(channel_id)
    except NotificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
