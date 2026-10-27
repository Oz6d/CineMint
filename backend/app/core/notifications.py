"""通知渠道目录、配置加密与发送。

配置中的密钥字段用 Fernet 加密后存入 notification_channels.config；
对外返回时只给出「已设置」标记，不返回密文或明文。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import smtplib
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from typing import Any, Callable
from xml.sax.saxutils import escape as xml_escape

import httpx

from ..db import dumps, execute, execute_count, loads, now, query_all, query_one
from ..security import decrypt, encrypt

TIMEOUT = 12.0


class NotificationError(RuntimeError):
    pass


EVENTS = [
    {"key": "run.summary", "label": "运行摘要"},
    {"key": "transfer.success", "label": "转存成功"},
    {"key": "transfer.failed", "label": "转存失败"},
    {"key": "organize.success", "label": "整理成功"},
    {"key": "organize.failed", "label": "整理失败"},
    {"key": "item.unmatched", "label": "无法识别"},
    {"key": "item.review", "label": "低置信度待确认"},
    {"key": "item.duplicate", "label": "普通重复"},
    {"key": "item.replaced", "label": "V2 替换"},
    {"key": "run.failed", "label": "运行失败"},
]
EVENT_KEYS = {item["key"] for item in EVENTS}


def field(
    key: str,
    label: str,
    *,
    kind: str = "text",
    required: bool = False,
    secret: bool = False,
    default: Any = "",
    placeholder: str = "",
    help_text: str = "",
    options: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "type": kind,
        "required": required,
        "secret": secret,
        "default": default,
        "placeholder": placeholder,
        "help": help_text,
        "options": options or [],
    }


CHANNEL_CATALOG: list[dict[str, Any]] = [
    {
        "type": "webhook",
        "label": "自定义 Webhook",
        "description": "GET / POST，支持 $title / $content 占位符。",
        "fields": [
            field("url", "Webhook 地址", required=True, secret=True,
                  placeholder="https://example.com/hook"),
            field("method", "请求方式", kind="select", default="POST", options=["POST", "GET"]),
            field("content_type", "内容类型", kind="select", default="application/json",
                  options=["application/json", "application/x-www-form-urlencoded", "text/plain"]),
            field("headers", "请求头", kind="textarea", placeholder='{"Authorization": "Bearer xxx"}'),
            field("body", "请求体模板", kind="textarea",
                  default='{"title": "$title", "content": "$content"}',
                  help_text="JSON 用引号包裹占位符；表单用 key=$title&content=$content。"),
        ],
    },
    {
        "type": "bark",
        "label": "Bark",
        "description": "iOS Bark 推送，支持自建服务端。",
        "fields": [
            field("server", "服务地址", default="https://api.day.app"),
            field("device_key", "Device Key", required=True, secret=True),
            field("group", "分组"),
            field("sound", "铃声"),
            field("icon", "图标 URL"),
        ],
    },
    {
        "type": "dingtalk",
        "label": "钉钉",
        "description": "自定义机器人，支持加签。",
        "fields": [
            field("webhook", "Webhook 地址", required=True, secret=True),
            field("secret", "加签 Secret", secret=True),
            field("msgtype", "消息类型", kind="select", default="markdown", options=["markdown", "text"]),
            field("at_all", "提醒所有人", kind="bool", default=False),
        ],
    },
    {
        "type": "feishu",
        "label": "飞书",
        "description": "飞书自定义机器人，支持签名校验。",
        "fields": [
            field("webhook", "Webhook 地址", required=True, secret=True),
            field("secret", "签名 Secret", secret=True),
        ],
    },
    {
        "type": "gocqhttp",
        "label": "go-cqhttp",
        "description": "通过 go-cqhttp / NapCat 的 HTTP API 发送 QQ 消息。",
        "fields": [
            field("endpoint", "API 地址", default="http://127.0.0.1:5700"),
            field("access_token", "Access Token", secret=True),
            field("target_type", "目标类型", kind="select", default="private", options=["private", "group"]),
            field("target_id", "QQ / 群号", required=True),
        ],
    },
    {
        "type": "gotify",
        "label": "Gotify",
        "description": "自建 Gotify 推送。",
        "fields": [
            field("server", "服务地址", default="https://gotify.example.com"),
            field("token", "应用 Token", required=True, secret=True),
            field("priority", "优先级", kind="int", default=5),
        ],
    },
    {
        "type": "igot",
        "label": "iGot",
        "description": "iGot 推送。",
        "fields": [
            field("server", "服务地址", default="https://push.hellyw.com"),
            field("key", "推送 Key", required=True, secret=True),
        ],
    },
    {
        "type": "serverchan",
        "label": "Server酱",
        "description": "Server酱 Turbo / 自建服务。",
        "fields": [
            field("server", "服务地址", default="https://sctapi.ftqq.com"),
            field("sendkey", "SendKey", required=True, secret=True),
        ],
    },
    {
        "type": "pushdeer",
        "label": "PushDeer",
        "description": "PushDeer / 自建服务。",
        "fields": [
            field("endpoint", "接口地址", default="https://api2.pushdeer.com/message/push"),
            field("pushkey", "PushKey", required=True, secret=True),
        ],
    },
    {
        "type": "synology_chat",
        "label": "Synology Chat",
        "description": "群晖 Chat 传入 Webhook。",
        "fields": [
            field("webhook", "Webhook 地址", required=True, secret=True),
        ],
    },
    {
        "type": "pushplus",
        "label": "PushPlus",
        "description": "PushPlus 微信推送。",
        "fields": [
            field("endpoint", "接口地址", default="https://www.pushplus.plus/send"),
            field("token", "Token", required=True, secret=True),
            field("topic", "群组编码"),
            field("channel", "渠道"),
            field("template", "模板", kind="select", default="markdown",
                  options=["markdown", "html", "txt"]),
        ],
    },
    {
        "type": "weplus_bot",
        "label": "微加机器人",
        "description": "微加机器人微信群推送。",
        "fields": [
            field("endpoint", "接口地址", default="https://www.weplusbot.com/send"),
            field("token", "Token", required=True, secret=True),
            field("receiver", "接收方 / 群 ID"),
            field("template", "消息格式", kind="select", default="txt", options=["txt", "html"]),
            field("version", "账号版本", kind="select", default="pro", options=["pro", "personal"]),
        ],
    },
    {
        "type": "qmsg",
        "label": "Qmsg",
        "description": "Qmsg酱 QQ 推送。",
        "fields": [
            field("server", "服务地址", default="https://qmsg.zendee.cn"),
            field("key", "Key", required=True, secret=True),
            field("group", "群号（留空发给默认 QQ）"),
        ],
    },
    {
        "type": "wecom_app",
        "label": "企业微信应用",
        "description": "企业微信自建应用消息。",
        "fields": [
            field("corp_id", "企业 ID", required=True),
            field("agent_id", "应用 AgentId", required=True),
            field("secret", "应用 Secret", required=True, secret=True),
            field("to_user", "接收成员", default="@all"),
            field("api_base", "API 地址", default="https://qyapi.weixin.qq.com/cgi-bin"),
        ],
    },
    {
        "type": "wecom_bot",
        "label": "企业微信机器人",
        "description": "企业微信群机器人。",
        "fields": [
            field("webhook", "Webhook 地址", required=True, secret=True),
            field("at_all", "提醒所有人", kind="bool", default=False),
            field("mentioned_list", "提醒成员（逗号分隔）"),
            field("mentioned_mobile_list", "提醒手机号（逗号分隔）"),
        ],
    },
    {
        "type": "telegram",
        "label": "Telegram",
        "description": "Telegram Bot 消息。",
        "fields": [
            field("api_base", "API 地址", default="https://api.telegram.org"),
            field("bot_token", "Bot Token", required=True, secret=True),
            field("chat_id", "Chat ID", required=True),
            field("parse_mode", "解析模式", kind="select", default="", options=["", "Markdown", "HTML"]),
            field("disable_notification", "静默发送", kind="bool", default=False),
        ],
    },
    {
        "type": "wechaty",
        "label": "智能微秘书",
        "description": "智能微秘书微信机器人（Aibotk 接口）。",
        "fields": [
            field("endpoint", "API 地址", default="https://api-bot.aibotk.com/openapi/v1/chat"),
            field("api_key", "API Key", required=True, secret=True),
            field("target_type", "目标类型", kind="select", default="contact", options=["contact", "room"]),
            field("target", "联系人 / 群名称", required=True),
        ],
    },
    {
        "type": "smtp",
        "label": "SMTP 邮件",
        "description": "通过 SMTP 发送邮件。",
        "fields": [
            field("host", "SMTP 服务器", required=True, placeholder="smtp.example.com"),
            field("port", "端口", kind="int", default=465),
            field("username", "用户名"),
            field("password", "密码 / 授权码", secret=True),
            field("from_addr", "发件地址"),
            field("to_addr", "收件地址", required=True, placeholder="a@example.com, b@example.com"),
            field("ssl", "使用 SSL", kind="bool", default=True),
            field("starttls", "使用 STARTTLS", kind="bool", default=False),
        ],
    },
    {
        "type": "pushme",
        "label": "PushMe",
        "description": "PushMe 推送。",
        "fields": [
            field("endpoint", "接口地址", default="https://push.i-i.me/"),
            field("push_key", "Push Key", required=True, secret=True),
        ],
    },
    {
        "type": "chronocat",
        "label": "Chronocat",
        "description": "Chronocat / Satori 协议 QQ 机器人。",
        "fields": [
            field("endpoint", "API 地址", default="http://127.0.0.1:5500"),
            field("token", "Token", secret=True),
            field("platform", "平台标识", default="chronocat"),
            field("self_id", "机器人 QQ（多账号时填写）"),
            field("channel_id", "目标 Channel ID", required=True),
        ],
    },
    {
        "type": "ntfy",
        "label": "Ntfy",
        "description": "Ntfy 推送，支持自建服务。",
        "fields": [
            field("server", "服务地址", default="https://ntfy.sh"),
            field("topic", "Topic", required=True),
            field("token", "访问 Token", secret=True),
            field("priority", "优先级", kind="int", default=3),
            field("tags", "标签（逗号分隔）"),
        ],
    },
    {
        "type": "dodo",
        "label": "DoDo",
        "description": "DoDo 开放平台机器人。",
        "fields": [
            field("endpoint", "API 地址", default="https://botopen.imdodo.com"),
            field("client_id", "Client ID", required=True),
            field("token", "Token", required=True, secret=True),
            field("channel_id", "频道 ID", required=True),
        ],
    },
    {
        "type": "wxpusher",
        "label": "WxPusher",
        "description": "WxPusher 微信推送。",
        "fields": [
            field("endpoint", "接口地址", default="https://wxpusher.zjiecode.com/api/send/message"),
            field("app_token", "AppToken", required=True, secret=True),
            field("uids", "UID（逗号分隔）"),
            field("topic_ids", "Topic ID（逗号分隔）"),
        ],
    },
]

CHANNEL_MAP = {item["type"]: item for item in CHANNEL_CATALOG}


def default_config(channel_type: str) -> dict[str, Any]:
    meta = CHANNEL_MAP.get(channel_type)
    if not meta:
        return {}
    return {item["key"]: item["default"] for item in meta["fields"]}


def normalize_events(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value) in EVENT_KEYS]


def public_config(channel_type: str, stored: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    meta = CHANNEL_MAP.get(channel_type)
    if not meta:
        return {}, []
    result: dict[str, Any] = {}
    secrets_set: list[str] = []
    for item in meta["fields"]:
        key = item["key"]
        value = stored.get(key, item["default"])
        if item["secret"]:
            if decrypt(str(value or "")):
                secrets_set.append(key)
            result[key] = ""
        else:
            result[key] = value if value is not None else item["default"]
    return result, secrets_set


def merge_secrets(
    channel_type: str,
    incoming: dict[str, Any],
    stored: dict[str, Any],
) -> dict[str, Any]:
    meta = CHANNEL_MAP.get(channel_type)
    if not meta:
        return incoming
    merged = dict(incoming)
    for item in meta["fields"]:
        if not item["secret"]:
            continue
        key = item["key"]
        if merged.get(key) in (None, "") and stored.get(key):
            merged[key] = decrypt(str(stored[key]))
    return merged


def normalize_config(
    channel_type: str,
    raw: dict[str, Any],
    *,
    require_secrets: bool = True,
) -> dict[str, Any]:
    meta = CHANNEL_MAP.get(channel_type)
    if not meta:
        raise NotificationError("不支持的通知渠道类型")
    raw = raw if isinstance(raw, dict) else {}
    result: dict[str, Any] = {}
    missing: list[str] = []
    for item in meta["fields"]:
        key = item["key"]
        value = raw.get(key, item["default"])
        if item["type"] == "bool":
            result[key] = bool(value)
        elif item["type"] == "int":
            try:
                result[key] = int(value if value not in (None, "") else item["default"])
            except (TypeError, ValueError):
                result[key] = int(item["default"] or 0)
        else:
            result[key] = str(value or "").strip()
        if item["required"] and not result[key]:
            if not (item["secret"] and not require_secrets):
                missing.append(item["label"])
    if missing:
        raise NotificationError("请填写：" + "、".join(missing))
    return result


def encode_config(channel_type: str, config: dict[str, Any]) -> dict[str, Any]:
    meta = CHANNEL_MAP.get(channel_type)
    if not meta:
        raise NotificationError("不支持的通知渠道类型")
    encoded: dict[str, Any] = {}
    for item in meta["fields"]:
        key = item["key"]
        value = config.get(key)
        if item["secret"]:
            encoded[key] = encrypt(str(value)) if value not in (None, "") else ""
        else:
            encoded[key] = value
    return encoded


def decode_config(channel_type: str, stored: dict[str, Any]) -> dict[str, Any]:
    meta = CHANNEL_MAP.get(channel_type)
    if not meta:
        return {}
    result: dict[str, Any] = {}
    for item in meta["fields"]:
        key = item["key"]
        value = stored.get(key, item["default"])
        result[key] = decrypt(str(value or "")) if item["secret"] else value
    return result


# ---------------------------------------------------------------------------
# HTTP helpers


def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            return client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise NotificationError(f"请求失败: {type(exc).__name__}") from exc


def _require_ok(response: httpx.Response, label: str = "") -> None:
    if 200 <= response.status_code < 300:
        return
    snippet = (response.text or "").strip().replace("\n", " ")[:200]
    prefix = f"{label} " if label else ""
    raise NotificationError(f"{prefix}HTTP {response.status_code}: {snippet}")


def _json_data(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _check_field(
    response: httpx.Response,
    ok_values: set[Any],
    *keys: str,
    label: str = "",
) -> None:
    _require_ok(response, label)
    data = _json_data(response)
    for key in keys:
        if key in data:
            if data[key] not in ok_values:
                raise NotificationError(f"{label} 返回失败: {data}")
            return


def _text(title: str, content: str) -> str:
    return f"{title}\n{content}".strip()


def _split(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").replace(";", ",").split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Adapters


def _send_webhook(config: dict[str, Any], title: str, content: str) -> None:
    url = _substitute(config.get("url", ""), title, content, escape="url")

    def escape_header(value: str) -> str:
        return value.replace("\r", " ").replace("\n", " ")

    headers: dict[str, str] = {}
    raw_headers = str(config.get("headers") or "").strip()
    if raw_headers:
        try:
            parsed = json.loads(raw_headers)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            headers = {str(key): escape_header(str(value)) for key, value in parsed.items()}
        else:
            for line in raw_headers.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                headers[key.strip()] = escape_header(
                    _substitute(value.strip(), title, content, escape="header")
                )

    method = str(config.get("method") or "POST").upper()
    if method == "GET":
        response = _request("GET", url, headers=headers)
        _require_ok(response)
        return

    content_type = str(config.get("content_type") or "application/json")
    body_template = str(config.get("body") or "").strip()
    if content_type == "application/json":
        body_template = body_template or '{"title": "$title", "content": "$content"}'
        body = _substitute(body_template, title, content, escape="json")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise NotificationError("Webhook 请求体不是合法 JSON") from exc
        response = _request("POST", url, headers=headers, json=payload)
    elif content_type == "application/x-www-form-urlencoded":
        body_template = body_template or "title=$title&content=$content"
        body = _substitute(body_template, title, content, escape="url")
        response = _request(
            "POST",
            url,
            headers=headers,
            data=dict(urllib.parse.parse_qsl(body, keep_blank_values=True)),
        )
    else:
        body = _substitute(body_template or _text(title, content), title, content, escape="raw")
        headers.setdefault("Content-Type", "text/plain; charset=utf-8")
        response = _request("POST", url, headers=headers, content=body.encode("utf-8"))
    _require_ok(response)


def _substitute(template: str, title: str, content: str, *, escape: str) -> str:
    values = {"$title": title, "$content": content}
    result = str(template or "")
    for token, value in values.items():
        if escape == "json":
            replacement = json.dumps(value)[1:-1]
        elif escape == "url":
            replacement = urllib.parse.quote_plus(value)
        elif escape == "header":
            replacement = value.replace("\r", " ").replace("\n", " ")
        else:
            replacement = value
        result = result.replace(token, replacement)
    return result


def _send_bark(config: dict[str, Any], title: str, content: str) -> None:
    server = str(config.get("server") or "https://api.day.app").rstrip("/")
    url = server if server.endswith("/push") else server + "/push"
    payload = {
        "device_key": config.get("device_key", ""),
        "title": title,
        "body": content,
    }
    for key in ("group", "sound", "icon"):
        if config.get(key):
            payload[key] = config[key]
    response = _request("POST", url, json=payload)
    _check_field(response, {200, 0}, "code", label="Bark")


def _send_dingtalk(config: dict[str, Any], title: str, content: str) -> None:
    url = str(config.get("webhook") or "")
    secret = str(config.get("secret") or "")
    if secret:
        timestamp = str(round(time.time() * 1000))
        digest = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}\n{secret}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("ascii"))
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}timestamp={timestamp}&sign={sign}"
    at_all = bool(config.get("at_all"))
    if str(config.get("msgtype") or "markdown") == "text":
        payload = {
            "msgtype": "text",
            "text": {"content": _text(title, content)},
            "at": {"isAtAll": at_all},
        }
    else:
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": content},
            "at": {"isAtAll": at_all},
        }
    response = _request("POST", url, json=payload)
    _check_field(response, {0}, "errcode", label="钉钉")


def _send_feishu(config: dict[str, Any], title: str, content: str) -> None:
    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": _text(title, content)},
    }
    secret = str(config.get("secret") or "")
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
        ).decode("ascii")
        payload["timestamp"] = timestamp
        payload["sign"] = sign
    response = _request("POST", str(config.get("webhook") or ""), json=payload)
    data = _json_data(response)
    _require_ok(response, "飞书")
    if data.get("code") not in (0, None) and data.get("StatusCode") not in (0, None):
        raise NotificationError(f"飞书返回失败: {data}")


def _send_gocqhttp(config: dict[str, Any], title: str, content: str) -> None:
    endpoint = str(config.get("endpoint") or "").rstrip("/")
    target_type = str(config.get("target_type") or "private")
    path = "/send_group_msg" if target_type == "group" else "/send_private_msg"
    target_key = "group_id" if target_type == "group" else "user_id"
    raw_target = str(config.get("target_id") or "")
    target: Any = int(raw_target) if raw_target.isdigit() else raw_target
    headers = {}
    token = str(config.get("access_token") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = _request(
        "POST",
        endpoint + path,
        headers=headers,
        json={target_key: target, "message": _text(title, content)},
    )
    _require_ok(response, "go-cqhttp")
    data = _json_data(response)
    if data.get("status") not in (None, "ok") and data.get("retcode") not in (None, 0):
        raise NotificationError(f"go-cqhttp 返回失败: {data}")


def _send_gotify(config: dict[str, Any], title: str, content: str) -> None:
    server = str(config.get("server") or "").rstrip("/")
    response = _request(
        "POST",
        server + "/message",
        headers={"X-Gotify-Key": str(config.get("token") or "")},
        json={
            "title": title,
            "message": content,
            "priority": int(config.get("priority") or 5),
        },
    )
    _require_ok(response, "Gotify")


def _send_igot(config: dict[str, Any], title: str, content: str) -> None:
    server = str(config.get("server") or "https://push.hellyw.com").rstrip("/")
    key = str(config.get("key") or "")
    response = _request("POST", f"{server}/{key}", data={"title": title, "content": content})
    _require_ok(response, "iGot")
    data = _json_data(response)
    if data.get("ret") not in (None, 0) or data.get("code") not in (None, 0):
        raise NotificationError(f"iGot 返回失败: {data}")


def _send_serverchan(config: dict[str, Any], title: str, content: str) -> None:
    server = str(config.get("server") or "https://sctapi.ftqq.com").rstrip("/")
    sendkey = str(config.get("sendkey") or "")
    response = _request("POST", f"{server}/{sendkey}.send", data={"title": title, "desp": content})
    _require_ok(response, "Server酱")
    data = _json_data(response)
    if data.get("code") not in (None, 0) or data.get("errno") not in (None, 0):
        raise NotificationError(f"Server酱返回失败: {data}")


def _send_pushdeer(config: dict[str, Any], title: str, content: str) -> None:
    response = _request(
        "POST",
        str(config.get("endpoint") or "https://api2.pushdeer.com/message/push"),
        data={
            "pushkey": config.get("pushkey", ""),
            "text": title,
            "desp": content,
            "type": "text",
        },
    )
    _require_ok(response, "PushDeer")
    data = _json_data(response)
    if data.get("code") not in (None, 0):
        raise NotificationError(f"PushDeer 返回失败: {data}")


def _send_synology_chat(config: dict[str, Any], title: str, content: str) -> None:
    payload = json.dumps({"text": _text(title, content)}, ensure_ascii=False)
    response = _request(
        "POST",
        str(config.get("webhook") or ""),
        data={"payload": payload},
    )
    _require_ok(response, "Synology Chat")


def _send_pushplus(config: dict[str, Any], title: str, content: str) -> None:
    payload = {
        "token": config.get("token", ""),
        "title": title,
        "content": content,
        "template": str(config.get("template") or "markdown"),
    }
    for key in ("topic", "channel"):
        if config.get(key):
            payload[key] = config[key]
    response = _request(
        "POST",
        str(config.get("endpoint") or "https://www.pushplus.plus/send"),
        json=payload,
    )
    _check_field(response, {200, 0}, "code", label="PushPlus")


def _send_weplus_bot(config: dict[str, Any], title: str, content: str) -> None:
    payload = {
        "token": config.get("token", ""),
        "title": title,
        "content": content,
    }
    for key in ("receiver", "template", "version"):
        if config.get(key):
            payload[key] = config[key]
    response = _request(
        "POST",
        str(config.get("endpoint") or "https://www.weplusbot.com/send"),
        json=payload,
    )
    _check_field(response, {200}, "code", label="微加机器人")


def _send_qmsg(config: dict[str, Any], title: str, content: str) -> None:
    server = str(config.get("server") or "https://qmsg.zendee.cn").rstrip("/")
    key = str(config.get("key") or "")
    payload = {"msg": _text(title, content)}
    if config.get("group"):
        payload["group"] = config["group"]
    response = _request("POST", f"{server}/v3/send/{key}", data=payload)
    _require_ok(response, "Qmsg")
    data = _json_data(response)
    if data.get("success") is False or data.get("code") not in (None, 0):
        raise NotificationError(f"Qmsg 返回失败: {data}")


_wework_lock = threading.Lock()
_wework_tokens: dict[str, tuple[str, float]] = {}


def _wecom_access_token(config: dict[str, Any]) -> str:
    corp_id = str(config.get("corp_id") or "")
    secret = str(config.get("secret") or "")
    cache_key = f"{corp_id}:{secret}"
    now_ts = time.time()
    with _wework_lock:
        cached = _wework_tokens.get(cache_key)
        if cached and cached[1] > now_ts + 60:
            return cached[0]
    base = str(config.get("api_base") or "https://qyapi.weixin.qq.com/cgi-bin").rstrip("/")
    response = _request(
        "GET",
        base + "/gettoken",
        params={"corpid": corp_id, "corpsecret": secret},
    )
    _require_ok(response, "企业微信")
    data = _json_data(response)
    if data.get("errcode") not in (0, None) or not data.get("access_token"):
        raise NotificationError(f"企业微信获取 Token 失败: {data}")
    token = str(data["access_token"])
    expires = int(data.get("expires_in") or 7200)
    with _wework_lock:
        _wework_tokens[cache_key] = (token, now_ts + expires)
    return token


def _send_wecom_app(config: dict[str, Any], title: str, content: str) -> None:
    token = _wecom_access_token(config)
    base = str(config.get("api_base") or "https://qyapi.weixin.qq.com/cgi-bin").rstrip("/")
    try:
        agent_id: Any = int(config.get("agent_id") or 0)
    except (TypeError, ValueError):
        agent_id = config.get("agent_id", "")
    response = _request(
        "POST",
        base + "/message/send",
        params={"access_token": token},
        json={
            "touser": str(config.get("to_user") or "@all"),
            "msgtype": "text",
            "agentid": agent_id,
            "text": {"content": _text(title, content)},
        },
    )
    _check_field(response, {0}, "errcode", label="企业微信应用")


def _send_wecom_bot(config: dict[str, Any], title: str, content: str) -> None:
    text: dict[str, Any] = {"content": _text(title, content)}
    mentioned = _split(config.get("mentioned_list", ""))
    mentioned_mobile = _split(config.get("mentioned_mobile_list", ""))
    if config.get("at_all"):
        mentioned.append("@all")
    if mentioned:
        text["mentioned_list"] = mentioned
    if mentioned_mobile:
        text["mentioned_mobile_list"] = mentioned_mobile
    response = _request(
        "POST",
        str(config.get("webhook") or ""),
        json={"msgtype": "text", "text": text},
    )
    _check_field(response, {0}, "errcode", label="企业微信机器人")


def _send_telegram(config: dict[str, Any], title: str, content: str) -> None:
    base = str(config.get("api_base") or "https://api.telegram.org").rstrip("/")
    token = str(config.get("bot_token") or "")
    payload: dict[str, Any] = {
        "chat_id": config.get("chat_id", ""),
        "text": _text(title, content),
        "disable_notification": bool(config.get("disable_notification")),
        "disable_web_page_preview": True,
    }
    if config.get("parse_mode"):
        payload["parse_mode"] = config["parse_mode"]
    response = _request("POST", f"{base}/bot{token}/sendMessage", json=payload)
    _require_ok(response, "Telegram")
    data = _json_data(response)
    if data.get("ok") is False:
        raise NotificationError(f"Telegram 返回失败: {data.get('description') or data}")


def _send_wechaty(config: dict[str, Any], title: str, content: str) -> None:
    endpoint = str(config.get("endpoint") or "").rstrip("/")
    target_type = str(config.get("target_type") or "contact")
    path = "/room" if target_type == "room" else "/contact"
    target_key = "roomName" if target_type == "room" else "name"
    payload = {
        "apiKey": config.get("api_key", ""),
        target_key: config.get("target", ""),
        "message": {"type": 1, "content": _text(title, content)},
    }
    response = _request("POST", endpoint + path, json=payload)
    _require_ok(response, "智能微秘书")
    data = _json_data(response)
    if data.get("code") not in (None, 0):
        raise NotificationError(f"智能微秘书返回失败: {data}")


def _send_smtp(config: dict[str, Any], title: str, content: str) -> None:
    host = str(config.get("host") or "")
    port = int(config.get("port") or 465)
    username = str(config.get("username") or "")
    password = str(config.get("password") or "")
    sender = str(config.get("from_addr") or username)
    recipients = _split(config.get("to_addr", ""))
    if not sender:
        raise NotificationError("SMTP 发件地址为空")
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(content)
    try:
        if config.get("ssl", True):
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT)
        else:
            server = smtplib.SMTP(host, port, timeout=TIMEOUT)
        try:
            server.ehlo()
            if not config.get("ssl", True) and config.get("starttls"):
                server.starttls()
                server.ehlo()
            if username:
                server.login(username, password)
            server.send_message(message)
        finally:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass
    except (smtplib.SMTPException, OSError) as exc:
        raise NotificationError(f"SMTP 发送失败: {type(exc).__name__}: {exc}") from exc


def _send_pushme(config: dict[str, Any], title: str, content: str) -> None:
    endpoint = str(config.get("endpoint") or "https://push.i-i.me/")
    response = _request(
        "POST",
        endpoint,
        data={
            "push_key": config.get("push_key", ""),
            "title": title,
            "content": content,
            "type": "text",
        },
    )
    _require_ok(response, "PushMe")
    text = (response.text or "").strip()
    if text.lower() == "success":
        return
    data = _json_data(response)
    if data.get("code") not in (0, 200) or data.get("success") is False:
        raise NotificationError(f"PushMe 返回失败: {text[:120] or data}")


def _send_chronocat(config: dict[str, Any], title: str, content: str) -> None:
    endpoint = str(config.get("endpoint") or "").rstrip("/")
    token = str(config.get("token") or "")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if config.get("platform"):
        headers["Satori-Platform"] = str(config["platform"])
    if config.get("self_id"):
        headers["Satori-User-Id"] = str(config["self_id"])
    message = f"<message><text>{xml_escape(_text(title, content))}</text></message>"
    response = _request(
        "POST",
        endpoint + "/v1/message.create",
        headers=headers,
        json={"channel_id": config.get("channel_id", ""), "content": message},
    )
    _require_ok(response, "Chronocat")


def _send_ntfy(config: dict[str, Any], title: str, content: str) -> None:
    server = str(config.get("server") or "https://ntfy.sh").rstrip("/")
    headers = {}
    token = str(config.get("token") or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload: dict[str, Any] = {
        "topic": config.get("topic", ""),
        "title": title,
        "message": content,
        "priority": int(config.get("priority") or 3),
    }
    tags = _split(config.get("tags", ""))
    if tags:
        payload["tags"] = tags
    response = _request("POST", server + "/", headers=headers, json=payload)
    _require_ok(response, "Ntfy")


def _send_dodo(config: dict[str, Any], title: str, content: str) -> None:
    endpoint = str(config.get("endpoint") or "https://botopen.imdodo.com").rstrip("/")
    token = str(config.get("token") or "")
    client_id = str(config.get("client_id") or "")
    headers = {"Authorization": f"Bot {client_id}.{token}"}
    response = _request(
        "POST",
        endpoint + "/api/v2/channel/message/send",
        headers=headers,
        json={
            "channelId": config.get("channel_id", ""),
            "messageType": 1,
            "messageBody": {"content": _text(title, content)},
        },
    )
    _require_ok(response, "DoDo")
    data = _json_data(response)
    if data.get("status") not in (None, 0):
        raise NotificationError(f"DoDo 返回失败: {data}")


def _send_wxpusher(config: dict[str, Any], title: str, content: str) -> None:
    payload: dict[str, Any] = {
        "appToken": config.get("app_token", ""),
        "content": _text(title, content),
        "summary": title[:100],
        "contentType": 1,
    }
    uids = _split(config.get("uids", ""))
    topic_ids = [int(item) for item in _split(config.get("topic_ids", "")) if item.isdigit()]
    if uids:
        payload["uids"] = uids
    if topic_ids:
        payload["topicIds"] = topic_ids
    response = _request(
        "POST",
        str(config.get("endpoint") or "https://wxpusher.zjiecode.com/api/send/message"),
        json=payload,
    )
    _check_field(response, {1000, 0}, "code", label="WxPusher")


ADAPTERS: dict[str, Callable[[dict[str, Any], str, str], None]] = {
    "webhook": _send_webhook,
    "bark": _send_bark,
    "dingtalk": _send_dingtalk,
    "feishu": _send_feishu,
    "gocqhttp": _send_gocqhttp,
    "gotify": _send_gotify,
    "igot": _send_igot,
    "serverchan": _send_serverchan,
    "pushdeer": _send_pushdeer,
    "synology_chat": _send_synology_chat,
    "pushplus": _send_pushplus,
    "weplus_bot": _send_weplus_bot,
    "qmsg": _send_qmsg,
    "wecom_app": _send_wecom_app,
    "wecom_bot": _send_wecom_bot,
    "telegram": _send_telegram,
    "wechaty": _send_wechaty,
    "smtp": _send_smtp,
    "pushme": _send_pushme,
    "chronocat": _send_chronocat,
    "ntfy": _send_ntfy,
    "dodo": _send_dodo,
    "wxpusher": _send_wxpusher,
}


def send_to_config(channel_type: str, config: dict[str, Any], title: str, content: str) -> None:
    adapter = ADAPTERS.get(channel_type)
    if adapter is None:
        raise NotificationError("不支持的通知渠道类型")
    adapter(config, title, content)


# ---------------------------------------------------------------------------
# Persistence and dispatch


def channel_row(row: dict[str, Any]) -> dict[str, Any]:
    config, secrets_set = public_config(row["type"], loads(row.get("config"), {}) or {})
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "config": config,
        "secret_fields_set": secrets_set,
        "events": loads(row.get("events"), []) or [],
        "enabled": bool(row.get("enabled")),
        "last_test_at": row.get("last_test_at"),
        "last_test_ok": (
            None if row.get("last_test_ok") is None else bool(row.get("last_test_ok"))
        ),
        "last_error": row.get("last_error") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_channels() -> list[dict[str, Any]]:
    return [channel_row(row) for row in query_all("SELECT * FROM notification_channels ORDER BY id")]


def get_channel(channel_id: int) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM notification_channels WHERE id=?", (channel_id,))
    return channel_row(row) if row else None


def create_channel(data: dict[str, Any]) -> dict[str, Any]:
    channel_type = str(data.get("type") or "")
    if channel_type not in CHANNEL_MAP:
        raise NotificationError("不支持的通知渠道类型")
    config = normalize_config(channel_type, data.get("config") or {}, require_secrets=True)
    events = normalize_events(data.get("events"))
    channel_id = execute(
        "INSERT INTO notification_channels(name,type,config,events,enabled,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            str(data.get("name") or CHANNEL_MAP[channel_type]["label"]).strip()[:80],
            channel_type,
            dumps(encode_config(channel_type, config)),
            dumps(events),
            1 if data.get("enabled", True) else 0,
            now(),
            now(),
        ),
    )
    return get_channel(channel_id) or {}


def update_channel(channel_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM notification_channels WHERE id=?", (channel_id,))
    if not row:
        return None
    channel_type = row["type"]
    stored = loads(row.get("config"), {}) or {}
    incoming = data.get("config")
    if incoming is None:
        config = decode_config(channel_type, stored)
    else:
        merged = merge_secrets(channel_type, incoming, stored)
        config = normalize_config(channel_type, merged, require_secrets=False)
    events = normalize_events(data.get("events", loads(row.get("events"), []) or []))
    name = str(data.get("name") or row["name"]).strip()[:80]
    enabled = bool(data.get("enabled", bool(row["enabled"])))
    execute(
        "UPDATE notification_channels SET name=?, config=?, events=?, enabled=?, updated_at=? "
        "WHERE id=?",
        (name, dumps(encode_config(channel_type, config)), dumps(events), 1 if enabled else 0, now(), channel_id),
    )
    return get_channel(channel_id)


def delete_channel(channel_id: int) -> bool:
    return execute_count("DELETE FROM notification_channels WHERE id=?", (channel_id,)) > 0


def _log_delivery(channel_id: int | None, event: str, title: str, ok: bool, error: str = "") -> None:
    execute(
        "INSERT INTO notification_logs(channel_id,event,title,ok,error,created_at) VALUES(?,?,?,?,?,?)",
        (channel_id, event, title[:200], 1 if ok else 0, (error or "")[:2000] or None, now()),
    )
    execute(
        "DELETE FROM notification_logs WHERE id IN ("
        "SELECT id FROM notification_logs ORDER BY id DESC LIMIT -1 OFFSET 1000)"
    )


def _deliver(row: dict[str, Any], event: str, title: str, content: str) -> dict[str, Any]:
    try:
        stored = loads(row.get("config"), {}) or {}
        config = decode_config(row["type"], stored)
        send_to_config(row["type"], config, title, content)
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        _log_delivery(row["id"], event, title, False, message)
        execute(
            "UPDATE notification_channels SET last_error=?, updated_at=? WHERE id=?",
            (message[:2000], now(), row["id"]),
        )
        return {"channel_id": row["id"], "name": row["name"], "ok": False, "error": message}
    _log_delivery(row["id"], event, title, True)
    execute(
        "UPDATE notification_channels SET last_error='', updated_at=? WHERE id=?",
        (now(), row["id"]),
    )
    return {"channel_id": row["id"], "name": row["name"], "ok": True, "error": ""}


def send_event(
    event: str,
    title: str,
    content: str,
    *,
    channel_ids: list[int] | None = None,
    match_events: list[str] | None = None,
) -> dict[str, Any]:
    """发送通知。

    match_events 用于「一次运行合并成一条推送」：渠道勾选的事件只要命中
    本次运行实际发生的任一事件，就接收这条合并消息；不传时仍按单一
    event 精确过滤，保持旧行为。
    """
    rows = query_all("SELECT * FROM notification_channels WHERE enabled=1 ORDER BY id")
    if channel_ids:
        wanted = {int(item) for item in channel_ids}
        rows = [row for row in rows if int(row["id"]) in wanted]
    wanted_events = set(match_events) if match_events else {event}
    selected = []
    for row in rows:
        events = loads(row.get("events"), []) or []
        if events and not wanted_events.intersection(events):
            continue
        selected.append(row)
    if not selected:
        return {"sent": 0, "failed": 0, "results": []}
    with ThreadPoolExecutor(max_workers=min(5, len(selected))) as pool:
        results = list(
            pool.map(lambda row: _deliver(row, event, title, content), selected)
        )
    return {
        "sent": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }


def test_channel(channel_id: int) -> dict[str, Any]:
    row = query_one("SELECT * FROM notification_channels WHERE id=?", (channel_id,))
    if not row:
        raise NotificationError("通知渠道不存在")
    title = "影序通知测试"
    content = "这是一条测试消息，收到即表示渠道配置可用。"
    result = _deliver(row, "test", title, content)
    execute(
        "UPDATE notification_channels SET last_test_at=?, last_test_ok=?, updated_at=? WHERE id=?",
        (now(), 1 if result["ok"] else 0, now(), channel_id),
    )
    if not result["ok"]:
        raise NotificationError(result["error"])
    return result


def test_config(channel_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    config = normalize_config(channel_type, raw, require_secrets=True)
    title = "影序通知测试"
    content = "这是一条测试消息，收到即表示渠道配置可用。"
    try:
        send_to_config(channel_type, config, title, content)
    except Exception as exc:
        _log_delivery(None, "test", title, False, str(exc))
        raise NotificationError(str(exc) or type(exc).__name__) from exc
    _log_delivery(None, "test", title, True)
    return {"ok": True}
