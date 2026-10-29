"""订阅执行器：夸克分享 → 转存暂存区 → 扫描匹配 → 整理。

执行由调度器触发，也可以手动执行。所有运行都写入 subscription_runs
与 subscription_run_logs，文件是否处理过记录在 subscription_entries。
"""

from __future__ import annotations

import re
import time
import traceback
from collections import defaultdict
from datetime import datetime
from typing import Any

from ..db import dumps, execute, get_setting, loads, now, query_all, query_one
from ..security import decrypt, encrypt
from .notifications import send_event
from .openlist import OpenListError, refresh_directory
from .naming import COLLISION_POLICIES
from .parser import VIDEO_EXTS, file_ext, parse_media_name
from .pipeline import organize_items, scan_library
from .providers import SourceError, build_source, join, norm
from .quark import (
    QuarkClient,
    QuarkError,
    QuarkShareCancelledError,
    QuarkShareError,
    default_save_subdir,
    parse_share_link,
    safe_save_subdir,
    staging_paths,
)
from .replacements import EpisodeReplacement
from .subscription_runs import log_run, set_progress, update_run

DEFAULT_QPS = 2.0
DEFAULT_MAX_PER_RUN = 20
MAX_PER_RUN_LIMIT = 200
DEFAULT_VISIBILITY_TIMEOUT = 120
MAX_VISIBILITY_TIMEOUT = 900
TERMINAL_ENTRY_STATUSES = {"organized", "replaced", "duplicate", "skipped"}


class SubscriptionError(RuntimeError):
    pass


class SubscriptionConfigError(SubscriptionError):
    pass


def quark_client() -> QuarkClient:
    cookie = decrypt(str(get_setting("quark_cookie", "") or ""))
    if not cookie:
        raise SubscriptionError("请先在设置中填写夸克 Cookie")
    try:
        qps = float(get_setting("quark_qps", DEFAULT_QPS) or DEFAULT_QPS)
    except (TypeError, ValueError):
        qps = DEFAULT_QPS
    return QuarkClient(cookie, qps=max(0.2, min(qps, 20.0)))


def normalize_config(raw: Any, name: str) -> dict[str, Any]:
    try:
        data = dict(raw or {})
    except (TypeError, ValueError):
        data = {}
    share_url = str(data.get("share_url") or "").strip()[:1000]
    passcode = str(data.get("passcode") or "").strip()[:32]
    share_subdir = norm(str(data.get("share_subdir") or "")[:500])
    quark_root = norm(str(data.get("quark_root") or "")[:500])
    save_subdir = norm(str(data.get("save_subdir") or "")[:500])
    # 留空表示影视库根目录（与 RSS 订阅一致），不再默认落到 _订阅/<订阅名>。
    save_root = bool(data.get("save_root")) or not save_subdir
    if save_root:
        save_subdir = ""
    folder_id = str(data.get("quark_folder_id") or "").strip()[:128]
    folder_path = norm(str(data.get("quark_folder_path") or "")[:1000])
    if folder_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", folder_id):
        raise SubscriptionConfigError("夸克目录 ID 无效")
    if not folder_id:
        folder_path = ""
    for label, value in (
        ("分享内目录", share_subdir),
        ("夸克目录", quark_root),
        ("暂存目录", save_subdir),
        ("夸克目标目录", folder_path),
    ):
        if value.startswith("..") or "/../" in f"/{value}/":
            raise SubscriptionConfigError(f"{label}不能包含 ..")
    try:
        parse_share_link(share_url, passcode)
    except QuarkShareError as exc:
        raise SubscriptionConfigError(str(exc)) from exc
    keyword = str(data.get("keyword") or "").strip()[:1000]
    pattern = str(data.get("regex") or "").strip()[:2000]
    if pattern:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SubscriptionConfigError(f"正则表达式无效: {exc}") from exc
    extensions = _normalize_extensions(str(data.get("file_extensions") or ""))
    try:
        max_per_run = int(data.get("max_per_run") or DEFAULT_MAX_PER_RUN)
    except (TypeError, ValueError):
        max_per_run = DEFAULT_MAX_PER_RUN
    max_per_run = max(1, min(max_per_run, MAX_PER_RUN_LIMIT))
    try:
        visibility = int(data.get("visibility_timeout") or DEFAULT_VISIBILITY_TIMEOUT)
    except (TypeError, ValueError):
        visibility = DEFAULT_VISIBILITY_TIMEOUT
    visibility = max(10, min(visibility, MAX_VISIBILITY_TIMEOUT))
    collision = str(data.get("collision") or "").strip().lower()
    if collision and collision not in COLLISION_POLICIES:
        raise SubscriptionConfigError("同名文件处理方式无效")
    include_subdirs = data.get("include_subdirs", True)
    if isinstance(include_subdirs, str):
        include_subdirs = include_subdirs.strip().lower() not in {"0", "false", "no", "off"}
    else:
        include_subdirs = bool(include_subdirs)
    return {
        "share_url": share_url,
        "passcode": passcode,
        "share_subdir": share_subdir,
        "keyword": keyword,
        "regex": pattern,
        "file_extensions": extensions,
        "max_per_run": max_per_run,
        "save_subdir": save_subdir,
        "save_root": save_root,
        "quark_root": quark_root,
        "quark_folder_id": folder_id,
        "quark_folder_path": folder_path,
        "visibility_timeout": visibility,
        "collision": collision,
        "include_subdirs": include_subdirs,
    }


def _normalize_extensions(value: str) -> str:
    parts = [
        part.strip().lower()
        for part in re.split(r"[\s,，;；]+", value or "")
        if part.strip()
    ]
    result: list[str] = []
    for part in parts:
        if not part.startswith("."):
            part = "." + part
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", part):
            raise SubscriptionConfigError(f"无法识别的扩展名: {part}")
        result.append(part)
    return ",".join(dict.fromkeys(result))


def public_config(
    config: dict[str, Any], name: str, sub_type: str = "quark"
) -> dict[str, Any]:
    if sub_type == "rss":
        result = dict(config or {})
        if result.get("save_root"):
            result["save_subdir"] = ""
        else:
            result["save_subdir"] = safe_save_subdir(
                str(result.get("save_subdir") or ""), name
            )
        return result
    result = decode_config(config)
    result["passcode"] = ""
    result["passcode_set"] = bool(config.get("passcode"))
    save_subdir = str(result.get("save_subdir") or "")
    save_root = bool(result.get("save_root")) or not save_subdir
    result["save_subdir"] = (
        "" if save_root else safe_save_subdir(save_subdir, name)
    )
    result["save_root"] = save_root
    return result


def decode_config(config: dict[str, Any]) -> dict[str, Any]:
    result = dict(config or {})
    raw = str(result.get("passcode") or "")
    if raw.startswith("enc:"):
        result["passcode"] = decrypt(raw)
    return result


def encode_config(config: dict[str, Any]) -> dict[str, Any]:
    result = dict(config or {})
    if result.get("passcode"):
        result["passcode"] = encrypt(str(result["passcode"]))
    return result


def _allowed_extensions(config: dict[str, Any]) -> set[str]:
    raw = str(config.get("file_extensions") or "")
    if raw:
        return {part for part in raw.split(",") if part}
    return set(VIDEO_EXTS)


def is_candidate(file_name: str, config: dict[str, Any]) -> bool:
    if file_ext(file_name) not in _allowed_extensions(config):
        return False
    keyword = str(config.get("keyword") or "")
    if keyword:
        terms = [
            term.strip()
            for term in re.split(r"[\n,，;；]+", keyword)
            if term.strip()
        ]
        lowered = file_name.lower()
        if terms and not all(term.lower() in lowered for term in terms):
            return False
    pattern = str(config.get("regex") or "")
    if pattern and not re.search(pattern, file_name):
        return False
    return True


class RunHooks:
    """把管线的日志 / 进度转写为订阅运行记录。"""

    def __init__(self, run_id: int) -> None:
        self.run_id = run_id
        self.base = 0.0
        self.span = 100.0

    def phase(self, base: float, span: float) -> None:
        self.base = base
        self.span = span

    def log(self, message: str, level: str = "info") -> None:
        log_run(self.run_id, message, level)

    def update(self, **fields: Any) -> None:
        allowed = {"progress", "total", "done", "stats"}
        payload = {key: value for key, value in fields.items() if key in allowed}
        if "progress" in payload:
            try:
                raw = float(payload["progress"])
            except (TypeError, ValueError):
                raw = 0.0
            payload["progress"] = round(self.base + max(0.0, min(raw, 100.0)) * self.span / 100.0, 1)
        if payload:
            update_run(self.run_id, **payload)

    def cancelled(self) -> bool:
        row = query_one("SELECT status FROM subscription_runs WHERE id=?", (self.run_id,))
        return bool(row and row["status"] == "cancelled")


def _parse_entry_name(file_name: str):
    return parse_media_name(file_name)


def _upsert_entry(subscription_id: int, file: Any, share_id: str) -> dict[str, Any]:
    parsed = _parse_entry_name(file.name)
    raw = dumps(
        {
            "share_id": share_id,
            "size": file.size,
            "share_path": file.path,
        }
    )
    existing = query_one(
        "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_key=?",
        (subscription_id, file.fid),
    )
    values = (
        file.name,
        file.path,
        parsed.season,
        parsed.episode,
        int(parsed.version or 0),
        parsed.version_label or "",
        raw,
        now(),
    )
    if existing:
        execute(
            "UPDATE subscription_entries SET file_name=?, remote_path=?, season=?, episode=?, "
            "version=?, version_label=?, raw=?, updated_at=? WHERE id=?",
            (*values, existing["id"]),
        )
        return query_one(
            "SELECT * FROM subscription_entries WHERE id=?", (existing["id"],)
        ) or existing
    entry_id = execute(
        "INSERT INTO subscription_entries(subscription_id, remote_key, file_name, remote_path, "
        "season, episode, version, version_label, status, raw, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,'pending',?,?,?)",
        (
            subscription_id,
            file.fid,
            file.name,
            file.path,
            parsed.season,
            parsed.episode,
            int(parsed.version or 0),
            parsed.version_label or "",
            raw,
            now(),
            now(),
        ),
    )
    return query_one("SELECT * FROM subscription_entries WHERE id=?", (entry_id,)) or {}


def _reconcile_organized_entry(entry: dict[str, Any], stats: defaultdict[str, int]) -> bool:
    """订阅条目对应的条目已被手动/其他任务整理时，直接同步为已整理，避免反复失败。"""
    item_id = entry.get("item_id")
    if not item_id:
        return False
    item = query_one("SELECT status, target_path FROM items WHERE id=?", (int(item_id),))
    if not item or item.get("status") != "organized":
        return False
    _set_entry(
        entry["id"],
        status="organized",
        target_path=item.get("target_path"),
        error=None,
    )
    stats["skipped"] += 1
    return True


def _set_entry(entry_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    keys = ", ".join(f"{key}=?" for key in fields)
    execute(f"UPDATE subscription_entries SET {keys} WHERE id=?", (*fields.values(), entry_id))


def _entry_channel_ids(subscription: dict[str, Any]) -> list[int] | None:
    raw = loads(subscription.get("notify_channel_ids"), []) or []
    result: list[int] = []
    for value in raw:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result or None


def _notify(
    subscription: dict[str, Any],
    event: str,
    title: str,
    content: str,
) -> None:
    try:
        # 标题只保留任务名，避免「影序 · 名称 · 运行失败」过长；
        # 失败类型放进正文第一行，信息不丢。
        body = f"{title}\n{content}" if title else content
        send_event(
            event,
            str(subscription["name"] or "订阅"),
            body[:4000],
            channel_ids=_entry_channel_ids(subscription),
        )
    except Exception:
        # 通知失败不能影响订阅本身的执行结果。
        pass


NOTIFY_SECTION_ORDER = (
    "item.unmatched",
    "item.review",
    "transfer.failed",
    "item.duplicate",
    "item.skipped",
    "organize.failed",
    "item.replaced",
    "transfer.success",
    "organize.success",
)
_NOTIFY_SECTION_TITLE = {
    "item.unmatched": "无法识别",
    "item.review": "待确认",
    "transfer.failed": "转存失败",
    "item.duplicate": "重复",
    "item.skipped": "跳过",
    "organize.failed": "整理失败",
    "item.replaced": "V2 替换",
    "transfer.success": "转存完成",
    "organize.success": "整理完成",
}
_NOTIFY_SECTION_NOTE = {
    "item.unmatched": "请到「影视库 → 待确认」手动匹配；手动匹配后，下次更新的剧集会按学到的标题自动识别，并在订阅开启自动整理时直接整理。",
    "item.review": "请到「影视库 → 待确认」确认识别结果。",
    "item.duplicate": "已存在同集或已整理版本，未重复整理。",
    "item.skipped": "目标已有同名文件，按同名文件策略跳过，源文件仍在暂存目录。",
    "organize.failed": "请到「任务中心」查看失败原因。",
    "item.replaced": "旧版本文件已移入回收目录。",
    "transfer.failed": "请检查夸克 Cookie / 115 授权或稍后重试。",
}
NOTIFY_ITEM_LIMIT = 15
NOTIFY_CONTENT_LIMIT = 3800


def _local_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def _notify_counts(stats: dict[str, Any] | None) -> str:
    data = stats or {}
    transfer = data.get("transferred")
    if transfer is None:
        transfer = data.get("downloaded", 0)
    transfer = int(transfer or 0)
    adopted = int(data.get("adopted") or 0)
    transfer_text = f"转存成功 {transfer}"
    if adopted:
        transfer_text += f"（复用 {adopted}）"
    return (
        f"{transfer_text} · 失败 {int(data.get('failed') or 0)} · "
        f"整理 {int(data.get('organized') or 0)} · "
        f"未识别 {int(data.get('unmatched') or 0)} · "
        f"待确认 {int(data.get('review') or 0)}"
    )


class RunNotifier:
    """把一次订阅运行中的文件级事件合并成一条推送。

    结构：标题只用任务名；正文先给转存成功 / 失败 / 整理 / 未识别 /
    待确认计数，再按分类列文件明细，最后放时间。同类文件只出现一条
    消息，避免 3 个无法识别就发 3 条。
    """

    def __init__(self, subscription: dict[str, Any]) -> None:
        self.subscription = subscription
        self._sections: dict[str, list[str]] = {}
        self._events: set[str] = set()
        self._sent = False

    def add(self, event: str, line: str = "") -> None:
        self._events.add(event)
        bucket = self._sections.setdefault(event, [])
        if line:
            bucket.append(str(line))

    def flush(
        self,
        summary: str,
        stats: dict[str, Any] | None = None,
        *,
        force: bool = False,
    ) -> None:
        if self._sent:
            return
        if not self._sections and not force:
            # 没有新内容（例如只是定时检查、没有新文件）不推送，避免打扰。
            return
        self._sent = True
        # 推送结构：先关键计数，再文件明细，最后时间。
        lines = [_notify_counts(stats)]
        names = set(NOTIFY_SECTION_ORDER)
        for event in NOTIFY_SECTION_ORDER:
            bucket = self._sections.get(event)
            if not bucket:
                continue
            title = _NOTIFY_SECTION_TITLE.get(event, event)
            lines.append("")
            lines.append(f"【{title} · {len(bucket)}】")
            lines.append("")
            for text in bucket[:NOTIFY_ITEM_LIMIT]:
                lines.append(f"- {text}")
            if len(bucket) > NOTIFY_ITEM_LIMIT:
                lines.append(f"- …等 {len(bucket)} 个，其余请到影序查看")
            note = _NOTIFY_SECTION_NOTE.get(event)
            if note:
                lines.append(note)
        for event, bucket in self._sections.items():
            if event in names or not bucket:
                continue
            lines.append("")
            lines.append(f"【{event} · {len(bucket)}】")
            lines.append("")
            for text in bucket[:NOTIFY_ITEM_LIMIT]:
                lines.append(f"- {text}")
        # 只有异常强制推送时才补充错误摘要；正常运行的统计行已足够。
        if force and summary:
            lines.append("")
            lines.append(summary)
        lines.append("")
        lines.append(f"时间：{_local_stamp()}")
        content = "\n".join(lines)[:NOTIFY_CONTENT_LIMIT]
        try:
            send_event(
                "run.summary",
                str(self.subscription.get("name") or "订阅"),
                content,
                channel_ids=_entry_channel_ids(self.subscription),
                match_events=sorted(self._events | {"run.summary"}),
            )
        except Exception:
            # 通知失败不能影响订阅本身的执行结果。
            pass


def _format_episode(entry: dict[str, Any]) -> str:
    season = entry.get("season")
    episode = entry.get("episode")
    if season is not None and episode is not None:
        return f"S{int(season):02d}E{int(episode):02d}"
    return ""


def _episode_key(item: dict[str, Any]) -> tuple[str, int | None, int | None] | None:
    match = loads(item.get("match"), {}) or {}
    match_key = item.get("match_key")
    if not match_key and match.get("tmdb_id"):
        match_key = f"{match.get('media_type') or 'movie'}:{match.get('tmdb_id')}"
    if not match_key:
        return None
    season = item.get("season")
    episode = item.get("episode")
    media_type = match.get("media_type") or ""
    if media_type == "movie" or (season is None and episode is None):
        return (str(match_key), None, None)
    return (str(match_key), int(season) if season is not None else None, int(episode) if episode is not None else None)


def _find_existing_episodes(
    library_id: int, item: dict[str, Any], key: tuple[str, int | None, int | None]
) -> list[dict[str, Any]]:
    match_key, season, episode = key
    params: list[Any] = [library_id, match_key, item["id"]]
    sql = (
        "SELECT * FROM items WHERE library_id=? AND match_key=? AND status='organized' "
        "AND id<>?"
    )
    if season is None:
        sql += " AND season IS NULL AND episode IS NULL"
    else:
        sql += " AND season=? AND episode=?"
        params.extend([season, episode])
    sql += " ORDER BY version DESC, id DESC"
    return query_all(sql, tuple(params))


def _wait_destination(
    client: QuarkClient, folder_fid: str, names: set[str], timeout: float = 15.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while True:
        files = {
            entry.name: entry
            for entry in client.list_folder(folder_fid)
            if not entry.is_dir
        }
        last = files
        if names.issubset(files.keys()) or time.monotonic() >= deadline:
            return last
        time.sleep(1.5)


def _refresh_openlist_cache(
    source_row: dict[str, Any] | None, path: str, hooks: RunHooks
) -> None:
    if not source_row or not path:
        return
    try:
        if refresh_directory(source_row, path):
            hooks.log(f"已刷新 OpenList 目录缓存：{path}", "debug")
    except OpenListError as exc:
        hooks.log(f"OpenList 刷新目录失败：{exc}", "warn")


def _wait_visible(
    source: Any,
    entries: list[dict[str, Any]],
    timeout: float,
    hooks: RunHooks,
    *,
    source_row: dict[str, Any] | None = None,
    refresh_path: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pending = {entry["id"]: entry for entry in entries}
    visible: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(10.0, timeout)
    warned = False
    _refresh_openlist_cache(source_row, refresh_path, hooks)
    next_refresh = time.monotonic() + 15.0
    while pending:
        for entry_id, entry in list(pending.items()):
            if hooks.cancelled():
                break
            try:
                present = source.exists(entry.get("saved_path") or "")
            except SourceError:
                present = False
            if present:
                visible.append(entry)
                pending.pop(entry_id, None)
        if not pending:
            break
        if time.monotonic() >= deadline:
            break
        if not warned:
            hooks.log(f"等待 OpenList 同步转存文件（最多 {int(timeout)} 秒）", "debug")
            warned = True
        if source_row and refresh_path and time.monotonic() >= next_refresh:
            _refresh_openlist_cache(source_row, refresh_path, hooks)
            next_refresh = time.monotonic() + 15.0
        time.sleep(3.0)
    return visible, list(pending.values())


def _staging_for(
    library: dict[str, Any],
    config: dict[str, Any],
    subscription: dict[str, Any],
) -> tuple[str, list[str]]:
    raw_subdir = str(config.get("save_subdir") or "")
    save_subdir = (
        ""
        if config.get("save_root") or not raw_subdir
        else safe_save_subdir(raw_subdir, subscription["name"])
    )
    return staging_paths(
        library["root_path"] or "",
        save_subdir,
        cloud_root=str(config.get("quark_root") or ""),
    )


def _transfer(
    client: QuarkClient,
    share: Any,
    stoken: str,
    config: dict[str, Any],
    subscription: dict[str, Any],
    candidates: list[tuple[Any, dict[str, Any]]],
    hooks: RunHooks,
    stats: defaultdict[str, int],
    notifier: RunNotifier,
) -> list[dict[str, Any]]:
    library = query_one("SELECT * FROM libraries WHERE id=?", (subscription["library_id"],))
    if not library:
        raise SubscriptionError("订阅关联的影视库不存在")
    openlist_staging, quark_parts = _staging_for(library, config, subscription)
    if not openlist_staging:
        raise SubscriptionError("影视库根目录为空，无法确定暂存目录")
    hooks.log(f"暂存目录：{openlist_staging}")
    folder_fid = str(config.get("quark_folder_id") or "").strip()
    destination: dict[str, Any] | None = None
    if folder_fid:
        try:
            destination = {
                entry.name: entry
                for entry in client.list_folder(folder_fid)
                if not entry.is_dir
            }
        except QuarkError as exc:
            hooks.log(f"夸克目标目录不可用，改用路径定位：{exc}", "warn")
            folder_fid = ""
    if destination is None:
        folder_fid = client.ensure_folder_path(quark_parts)
        destination = {
            entry.name: entry
            for entry in client.list_folder(folder_fid)
            if not entry.is_dir
        }
    claimed: set[str] = set()
    adopted_names: set[str] = set()
    seen_names: set[str] = set()
    to_save: list[tuple[Any, dict[str, Any]]] = []
    retry_entries: list[dict[str, Any]] = []
    for file, entry in candidates:
        saved_path = join(openlist_staging, file.name)
        if file.name in destination and file.name not in claimed:
            claimed.add(file.name)
            _set_entry(entry["id"], status="saved", saved_path=saved_path, error=None)
            entry.update(status="saved", saved_path=saved_path, error=None)
            retry_entries.append(entry)
            adopted_names.add(entry["file_name"])
            stats["adopted"] += 1
            continue
        if file.name in seen_names or file.name in claimed:
            _set_entry(
                entry["id"],
                status="duplicate",
                error="目标暂存目录已有同名文件",
            )
            stats["duplicate"] += 1
            notifier.add("item.duplicate", f"{file.name}：目标暂存目录已有同名文件")
            continue
        if len(to_save) >= int(config.get("max_per_run") or DEFAULT_MAX_PER_RUN):
            stats["deferred"] += 1
            hooks.log(f"达到每次上限，剩余文件留到下次运行: {file.name}", "warn")
            continue
        seen_names.add(file.name)
        to_save.append((file, entry))

    if to_save:
        hooks.log(f"开始转存 {len(to_save)} 个文件到夸克暂存目录")
        try:
            saved = client.save_share_files(
                share.share_id,
                stoken,
                fids=[file.fid for file, _ in to_save],
                tokens=[file.token for file, _ in to_save],
                to_pdir_fid=folder_fid,
            )
            client.wait_task(saved.get("task_id") or "", timeout=120)
            names = {file.name for file, _ in to_save}
            files_now = _wait_destination(client, folder_fid, names)
            for file, entry in to_save:
                match = files_now.get(file.name)
                if not match:
                    _set_entry(
                        entry["id"],
                        status="failed",
                        error="转存任务结束，但目标目录里没有找到文件",
                    )
                    stats["failed"] += 1
                    continue
                saved_path = join(openlist_staging, file.name)
                _set_entry(entry["id"], status="saved", saved_path=saved_path, error=None)
                entry.update(status="saved", saved_path=saved_path, error=None)
                retry_entries.append(entry)
                stats["transferred"] += 1
        except QuarkError as exc:
            message = str(exc)[:500]
            for _, entry in to_save:
                _set_entry(entry["id"], status="failed", error=message)
            stats["failed"] += len(to_save)
            hooks.log(f"转存失败: {message}", "error")
            for file, _entry in to_save:
                notifier.add("transfer.failed", f"{file.name}：{message}")
    # 只推送本次真正新转存的文件；复用暂存目录文件不算新内容，
    # 否则每次定时检查都会因为「复用」再推一条。
    if stats["transferred"]:
        for entry in retry_entries:
            if entry.get("status") == "saved" and entry["file_name"] not in adopted_names:
                notifier.add("transfer.success", entry["file_name"])
    return retry_entries


def _record_item(entry: dict[str, Any], item: dict[str, Any]) -> None:
    _set_entry(
        entry["id"],
        item_id=item["id"],
        season=item.get("season"),
        episode=item.get("episode"),
        version=int(item.get("version") or 0),
        version_label=item.get("version_label") or "",
    )


def run_subscription(subscription: dict[str, Any], run_id: int) -> dict[str, Any]:
    hooks = RunHooks(run_id)
    notifier = RunNotifier(subscription)
    stats: defaultdict[str, int] = defaultdict(int)
    subscription_id = int(subscription["id"])
    started = time.monotonic()
    try:
        if subscription.get("type") == "rss":
            from .rss_subscription import run_rss_subscription

            return run_rss_subscription(subscription, run_id)
        if subscription.get("type") != "quark":
            raise SubscriptionError("不支持的订阅类型")
        if not subscription.get("library_id"):
            raise SubscriptionError("订阅没有关联影视库")
        config = normalize_config(
            decode_config(loads(subscription["config"], {})), subscription["name"]
        )
        library = query_one("SELECT * FROM libraries WHERE id=?", (subscription["library_id"],))
        if not library:
            raise SubscriptionError("订阅关联的影视库已删除")
        source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
        if not source_row:
            raise SubscriptionError("影视库的文件源已删除")
        source = build_source(source_row)
        hooks.log(f"开始检查订阅「{subscription['name']}」")
        hooks.phase(0, 55)
        client = quark_client()
        try:
            share = parse_share_link(config["share_url"], config["passcode"])
            hooks.log(f"读取夸克分享：{share.share_id}")
            stoken = client.share_token(share.share_id, share.passcode)
            share_files = client.walk_share(
                share.share_id,
                stoken,
                share_subdir=str(config.get("share_subdir") or ""),
                include_subdirs=bool(config.get("include_subdirs", True)),
            )
            stats["discovered"] = len(share_files)
            hooks.log(f"分享内共找到 {len(share_files)} 个文件")
            candidates: list[tuple[Any, dict[str, Any]]] = []
            for file in share_files:
                if not is_candidate(file.name, config):
                    stats["filtered"] += 1
                    continue
                entry = _upsert_entry(subscription_id, file, share.share_id)
                if _reconcile_organized_entry(entry, stats):
                    continue
                if entry.get("status") in TERMINAL_ENTRY_STATUSES:
                    stats["skipped"] += 1
                    continue
                candidates.append((file, entry))
            stats["matched_filter"] = len(candidates)
            hooks.log(
                f"过滤后 {len(candidates)} 个新文件，跳过已处理 {stats['skipped']} 个"
            )
            if not candidates:
                message = "没有发现需要处理的文件"
                update_run(run_id, progress=100, message=message)
                notifier.flush(message, dict(stats))
                return {"status": "success", "stats": dict(stats), "message": message}

            visible_entries = _transfer(
                client,
                share,
                stoken,
                config,
                subscription,
                candidates,
                hooks,
                stats,
                notifier,
            )
            hooks.phase(55, 35)
            hooks.update(progress=55, message="等待文件同步并扫描")
            if not visible_entries:
                message = "没有成功转存的文件"
                update_run(run_id, progress=100, message=message)
                notifier.flush(message, dict(stats))
                return {
                    "status": "partial" if stats["failed"] else "success",
                    "stats": dict(stats),
                    "message": message,
                }
            openlist_staging, _ = _staging_for(library, config, subscription)
            visible, hidden = _wait_visible(
                source,
                visible_entries,
                float(config.get("visibility_timeout") or DEFAULT_VISIBILITY_TIMEOUT),
                hooks,
                source_row=source_row,
                refresh_path=openlist_staging,
            )
            for entry in hidden:
                _set_entry(
                    entry["id"],
                    status="failed",
                    error="转存成功，但 OpenList 在限定时间内没有同步到该文件",
                )
                stats["failed"] += 1
                notifier.add(
                    "transfer.failed",
                    f"{entry['file_name']}：转存成功但 OpenList 未同步到文件",
                )
            if not visible:
                message = "转存成功，但文件未出现在 OpenList，稍后重试"
                hooks.log(message, "warn")
                notifier.flush(message, dict(stats), force=True)
                return {
                    "status": "partial",
                    "stats": dict(stats),
                    "message": message,
                }
            scan_result = scan_library(
                library["id"],
                hooks,
                root_path=openlist_staging,
                scope=openlist_staging,
                mark_missing=False,
                subscription_id=subscription_id,
            )
            items_by_path = {
                norm(item["source_path"]): item for item in scan_result.get("items", [])
            }
            item_ids = [item["id"] for item in scan_result.get("items", [])]
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                rows = query_all(
                    f"SELECT * FROM items WHERE id IN ({placeholders})", tuple(item_ids)
                )
                full_items = {row["id"]: row for row in rows}
            else:
                full_items = {}

            matched_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for entry in visible:
                path = norm(entry.get("saved_path") or "")
                scanned = items_by_path.get(path)
                if not scanned:
                    if _reconcile_organized_entry(entry, stats):
                        continue
                    _set_entry(
                        entry["id"],
                        status="failed",
                        error="扫描未发现该文件（可能被最小文件大小等设置过滤）",
                    )
                    stats["failed"] += 1
                    continue
                item = full_items.get(scanned["id"])
                if not item:
                    continue
                _record_item(entry, item)
                status = item.get("status")
                if status == "matched":
                    matched_pairs.append((item, entry))
                elif status == "review":
                    _set_entry(entry["id"], status="review")
                    stats["review"] += 1
                    if entry.get("status") != "review":
                        notifier.add("item.review", item["file_name"])
                elif status == "unmatched":
                    _set_entry(entry["id"], status="unmatched")
                    stats["unmatched"] += 1
                    if entry.get("status") != "unmatched":
                        notifier.add("item.unmatched", item["file_name"])
                elif status == "ignored":
                    _set_entry(entry["id"], status="ignored")
                    stats["ignored"] += 1
                elif status == "organized":
                    _set_entry(
                        entry["id"],
                        status="organized",
                        target_path=item.get("target_path"),
                    )
                    stats["skipped"] += 1
                else:
                    _set_entry(entry["id"], status=status or "pending")

            duplicates = _plan_duplicates(
                library, subscription, matched_pairs, hooks, stats, notifier
            )
            organize_queue = _plan_replacements_or_duplicates(
                library, subscription, duplicates, hooks, stats
            )
            hooks.phase(90, 10)
            hooks.update(progress=90, message="整理匹配文件")
            _run_organize(
                library,
                subscription,
                organize_queue,
                run_id,
                hooks,
                stats,
                notifier,
                collision=str(config.get("collision") or ""),
            )
        finally:
            client.close()

        message = _summary_message(stats)
        hooks.update(progress=100, message=message)
        notifier.flush(message, dict(stats))
        status = "partial" if stats["failed"] else "success"
        return {"status": status, "stats": dict(stats), "message": message}
    except QuarkShareCancelledError as exc:
        message = f"夸克分享已取消或失效: {exc}"
        hooks.log(message, "error")
        _notify(
            subscription,
            "share.cancelled",
            "分享已取消",
            f"订阅「{subscription['name']}」的夸克分享链接已取消或失效，请更新分享链接。\n{exc}",
        )
        raise SubscriptionError(message) from exc
    except QuarkShareError as exc:
        message = f"夸克分享不可用: {exc}"
        hooks.log(message, "error")
        _notify(subscription, "run.failed", "运行失败", message)
        raise SubscriptionError(message) from exc
    except Exception as exc:
        _notify(
            subscription,
            "run.failed",
            "运行失败",
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=2)}",
        )
        raise
    finally:
        stats["elapsed"] = int(time.monotonic() - started)


def _summary_message(stats: defaultdict[str, int]) -> str:
    return (
        f"转存 {stats['transferred']}，复用 {stats['adopted']}，"
        f"整理 {stats['organized']}，替换 {stats['replaced']}，"
        f"未识别 {stats['unmatched']}，待确认 {stats['review']}，"
        f"重复 {stats['duplicate']}，失败 {stats['failed']}，"
        f"跳过 {stats['skipped']}"
    )


def _plan_duplicates(
    library: dict[str, Any],
    subscription: dict[str, Any],
    pairs: list[tuple[dict[str, Any], dict[str, Any] | None]],
    hooks: RunHooks,
    stats: defaultdict[str, int],
    notifier: RunNotifier | None = None,
) -> list[dict[str, Any]]:
    """按「同一 TMDb + 季 + 集」分组，找出需要替换或只需上报的重复。"""
    groups: dict[tuple[str, int | None, int | None], list[tuple[dict[str, Any], dict[str, Any] | None]]] = {}
    singles: list[dict[str, Any]] = []
    for item, entry in pairs:
        key = _episode_key(item)
        if key is None:
            singles.append({"item": item, "entry": entry, "replace": []})
            continue
        groups.setdefault(key, []).append((item, entry))

    plans: list[dict[str, Any]] = []
    for key, group in groups.items():
        group.sort(
            key=lambda pair: (
                int(pair[0].get("version") or 0),
                int(pair[0].get("size") or 0),
            ),
            reverse=True,
        )
        primary_item, primary_entry = group[0]
        for item, entry in group[1:]:
            _mark_duplicate(
                subscription,
                item,
                entry,
                f"同一集重复文件，保留 {primary_item['file_name']}",
                stats,
                notifier,
            )
        existing = _find_existing_episodes(library["id"], primary_item, key)
        if existing:
            newest = existing[0]
            incoming_version = int(primary_item.get("version") or 0)
            old_version = int(newest.get("version") or 0)
            replace_enabled = bool(subscription.get("replace_versions")) and bool(
                subscription.get("auto_organize")
            )
            if incoming_version > 0 and incoming_version > old_version and replace_enabled:
                if len(existing) == 1:
                    hooks.log(
                        f"{primary_item['file_name']} 是 V{incoming_version}，"
                        f"将替换已整理的 {newest['file_name']}"
                    )
                    plans.append(
                        {
                            "item": primary_item,
                            "entry": primary_entry,
                            "replace": [newest],
                        }
                    )
                    continue
                hooks.log("同一集存在多个已整理版本，本次只上报重复，不自动替换", "warn")
            _mark_duplicate(
                subscription,
                primary_item,
                primary_entry,
                f"已整理过同集：{newest.get('target_path') or newest['file_name']}",
                stats,
                notifier,
            )
            continue
        plans.append({"item": primary_item, "entry": primary_entry, "replace": []})
    plans.extend(singles)
    return plans


def _mark_duplicate(
    subscription: dict[str, Any],
    item: dict[str, Any],
    entry: dict[str, Any] | None,
    message: str,
    stats: defaultdict[str, int],
    notifier: RunNotifier | None = None,
) -> None:
    if entry:
        _set_entry(entry["id"], status="duplicate", error=message)
    execute(
        "UPDATE items SET status='ignored', error=?, updated_at=? WHERE id=?",
        (message[:800], now(), item["id"]),
    )
    stats["duplicate"] += 1
    if notifier:
        notifier.add("item.duplicate", f"{item['file_name']}：{message}")


def _plan_replacements_or_duplicates(
    library: dict[str, Any],
    subscription: dict[str, Any],
    plans: list[dict[str, Any]],
    hooks: RunHooks,
    stats: defaultdict[str, int],
) -> list[dict[str, Any]]:
    if not subscription.get("auto_organize"):
        for plan in plans:
            entry = plan.get("entry")
            if not entry:
                continue
            if plan["replace"]:
                _set_entry(
                    entry["id"],
                    status="matched",
                    error="检测到更高版本，但订阅未启用自动整理",
                )
            else:
                _set_entry(entry["id"], status="matched")
        return []
    return plans


def _run_organize(
    library: dict[str, Any],
    subscription: dict[str, Any],
    plans: list[dict[str, Any]],
    run_id: int,
    hooks: RunHooks,
    stats: defaultdict[str, int],
    notifier: RunNotifier | None = None,
    collision: str = "",
) -> None:
    if not plans:
        return
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    if not source_row:
        raise SubscriptionError("影视库的文件源已删除")
    source = build_source(source_row)
    batch: list[dict[str, Any]] = []
    for plan in plans:
        if not plan["replace"]:
            batch.append(plan)
            continue
        old_item = plan["replace"][0]
        new_item = plan["item"]
        entry = plan.get("entry")
        try:
            with EpisodeReplacement(
                library, source, old_item, run_id=run_id
            ) as replacement:
                result = organize_items(
                    library["id"],
                    hooks,
                    item_ids=[new_item["id"]],
                    statuses=("matched",),
                    collision=collision,
                )
                outcome = _result_for(result, new_item["id"])
                if outcome and outcome.get("status") == "organized":
                    replacement.commit()
                    if entry:
                        _set_entry(
                            entry["id"],
                            status="organized",
                            target_path=outcome.get("target_path"),
                            error=None,
                        )
                    execute("DELETE FROM items WHERE id=?", (old_item["id"],))
                    stats["organized"] += 1
                    stats["replaced"] += 1
                    label = new_item.get("version_label") or "新版本"
                    hooks.log(f"V2 版本替换整理成功: {new_item['file_name']}（{label}）")
                    if notifier:
                        notifier.add(
                            "item.replaced",
                            f"{new_item['file_name']}（{label}）← "
                            f"{old_item.get('target_path') or old_item['file_name']}",
                        )
                elif outcome and outcome.get("status") == "skipped":
                    replacement.rollback()
                    message = outcome.get("error") or "目标已存在，按同名文件策略跳过"
                    if entry:
                        _set_entry(entry["id"], status="skipped", error=message[:800])
                    stats["skipped"] += 1
                    if notifier:
                        notifier.add("item.skipped", f"{new_item['file_name']}：{message}")
                elif outcome and outcome.get("status") == "review":
                    replacement.rollback()
                    message = outcome.get("error") or "缺少季/集号，请先修正识别信息"
                    if entry:
                        _set_entry(entry["id"], status="review", error=message[:800])
                    stats["review"] += 1
                    if notifier:
                        notifier.add("item.review", f"{new_item['file_name']}：{message}")
                else:
                    replacement.rollback()
                    error = (outcome or {}).get("error") or "整理失败"
                    if entry:
                        _set_entry(entry["id"], status="failed", error=error)
                    stats["failed"] += 1
                    if notifier:
                        notifier.add(
                            "organize.failed",
                            f"{new_item['file_name']}：{error}",
                        )
        except Exception as exc:
            if entry:
                _set_entry(entry["id"], status="failed", error=f"替换整理失败: {exc}"[:800])
            stats["failed"] += 1
            hooks.log(f"替换整理失败 {new_item['file_name']}: {exc}", "error")
            if notifier:
                notifier.add(
                    "organize.failed",
                    f"{new_item['file_name']}：替换整理失败 {exc}",
                )
    if not batch:
        return
    result = organize_items(
        library["id"],
        hooks,
        item_ids=[plan["item"]["id"] for plan in batch],
        statuses=("matched",),
        collision=collision,
    )
    outcomes = {row["item_id"]: row for row in result.get("results", [])}
    organized: list[str] = []
    failed: list[str] = []
    for plan in batch:
        item = plan["item"]
        entry = plan.get("entry")
        outcome = outcomes.get(item["id"])
        if outcome and outcome.get("status") == "organized":
            if entry:
                _set_entry(
                    entry["id"],
                    status="organized",
                    target_path=outcome.get("target_path"),
                    error=None,
                )
            stats["organized"] += 1
            organized.append(item["file_name"])
        elif outcome and outcome.get("status") == "skipped":
            message = outcome.get("error") or "目标已存在，按同名文件策略跳过"
            if entry:
                _set_entry(entry["id"], status="skipped", error=message[:800])
            stats["skipped"] += 1
            if notifier:
                notifier.add("item.skipped", f"{item['file_name']}：{message}")
        elif outcome and outcome.get("status") == "review":
            message = outcome.get("error") or "缺少季/集号，请先修正识别信息"
            if entry:
                _set_entry(entry["id"], status="review", error=message[:800])
            stats["review"] += 1
            if notifier:
                notifier.add("item.review", f"{item['file_name']}：{message}")
        else:
            error = (outcome or {}).get("error") or "整理失败"
            if entry:
                _set_entry(entry["id"], status="failed", error=error)
            stats["failed"] += 1
            failed.append(f"{item['file_name']}: {error}")
    if notifier:
        for name in organized:
            notifier.add("organize.success", name)
        for text in failed:
            notifier.add("organize.failed", text)


def _result_for(result: dict[str, Any], item_id: int) -> dict[str, Any] | None:
    for row in result.get("results", []):
        if int(row.get("item_id") or 0) == int(item_id):
            return row
    return None
