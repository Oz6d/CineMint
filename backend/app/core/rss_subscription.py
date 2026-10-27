"""RSS 订阅执行器：读取订阅源 → 115 离线下载 → 暂存区扫描 → 匹配整理。

条目按 feed URL + guid 记录在 subscription_entries，115 任务 ID 也存在该表。
下载完成的任务与扫描到的文件按任务名 / 标题 / 季集号做尽力归属。
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from ..db import dumps, execute, loads, now, query_all, query_one
from .cloud_paths import safe_save_subdir, staging_paths
from .openlist import OpenListError, refresh_directory
from .p115 import P115Error
from .p115_open import build_p115_client
from .parser import parse_media_name
from .pipeline import scan_library
from .providers import build_source, join, norm
from .rss import (
    FeedItem,
    RssError,
    fetch_feed,
    is_candidate,
    normalize_config,
    resolve_page_download,
    resolve_torrent_magnet,
)
from .subscriptions import (
    RunHooks,
    RunNotifier,
    SubscriptionError,
    _notify,
    _plan_duplicates,
    _plan_replacements_or_duplicates,
    _run_organize,
    _set_entry,
)

RSS_TERMINAL = {"organized", "replaced", "duplicate", "unsupported"}
RSS_SCANNABLE = {"saved", "matched", "review", "unmatched", "ignored"}
RSS_TASK_ACTIVE = {"queued", "downloading"}
DOWNLOAD_KINDS = {"magnet", "torrent", "ed2k", "http"}
BTIH_RE = re.compile(r"btih:([0-9a-z]+)", re.I)


def p115_client() -> Any:
    """执行器与目录接口共用的 115 客户端入口（Cookie 或开放平台）。"""
    return build_p115_client()


def _transfer_tokens(url: str = "", info_hash: str = "") -> set[str]:
    """任务关联标识：115 可能规范化下载 URL，info_hash 是稳定的备用键。"""
    tokens: set[str] = set()
    match = BTIH_RE.search(url or "")
    if match:
        tokens.add("bt:" + match.group(1).lower())
    value = str(info_hash or "").strip().lower()
    if value:
        tokens.add("ih:" + value)
    return tokens


def _entry_raw(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("raw")
    if isinstance(raw, dict):
        return dict(raw)
    return loads(raw, {}) or {}


def _upsert_entry(subscription_id: int, item: FeedItem) -> dict[str, Any]:
    parsed = parse_media_name(item.title)
    existing = query_one(
        "SELECT * FROM subscription_entries WHERE subscription_id=? AND remote_key=?",
        (subscription_id, item.remote_key),
    )
    # 重新读取 RSS 时保留上一轮写入的 115 任务名 / URL，供后续归属使用。
    raw = dumps(
        {
            **(_entry_raw(existing) if existing else {}),
            "feed_url": item.feed_url,
            "link": item.link,
            "download_url": item.download_url,
            "download_kind": item.download_kind,
            "size": item.size,
            "published_at": item.published_at,
        }
    )
    values = (
        item.title[:500],
        item.link,
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
            item.remote_key,
            item.title[:500],
            item.link,
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


def _set_queued(entry: dict[str, Any], task: dict[str, Any], staging: str) -> None:
    raw = _entry_raw(entry)
    raw["task_name"] = str(task.get("name") or "")[:500]
    raw["task_status"] = str(task.get("status") or "")
    raw["task_percent"] = float(task.get("percent") or 0)
    raw["task_url"] = str(task.get("url") or "")
    raw["task_info_hash"] = str(task.get("info_hash") or "")
    entry.update(
        status="queued",
        task_id=str(task.get("id") or ""),
        saved_path=join(staging, str(task.get("name") or entry["file_name"])),
        error=None,
        raw=raw,
    )
    _set_entry(
        entry["id"],
        status="queued",
        task_id=entry["task_id"],
        saved_path=entry["saved_path"],
        error=None,
        raw=dumps(raw),
    )


def _set_task_state(entry: dict[str, Any], task: dict[str, Any]) -> str:
    raw = _entry_raw(entry)
    raw["task_name"] = str(task.get("name") or raw.get("task_name") or "")[:500]
    raw["task_status"] = str(task.get("status") or "")
    raw["task_percent"] = float(task.get("percent") or 0)
    raw["task_url"] = str(task.get("url") or raw.get("task_url") or "")
    raw["task_info_hash"] = str(
        task.get("info_hash") or raw.get("task_info_hash") or ""
    )
    task_state = str(task.get("status") or "")
    if task_state == "done":
        _set_entry(
            entry["id"],
            status="saved",
            saved_path=entry.get("saved_path") or "",
            error=None,
            raw=dumps(raw),
        )
        entry.update(status="saved", raw=raw)
    elif task_state == "failed":
        _set_entry(
            entry["id"],
            status="failed",
            error=str(task.get("error") or "115 离线任务失败")[:500],
            raw=dumps(raw),
        )
        entry.update(status="failed", raw=raw)
    else:
        _set_entry(entry["id"], status="downloading", raw=dumps(raw))
        entry.update(status="downloading", raw=raw)
    return task_state


def _entry_title(entry: dict[str, Any]) -> str:
    return parse_media_name(str(entry.get("file_name") or "")).title.lower().strip()


def _attribute_items(
    entries: list[dict[str, Any]],
    items: list[dict[str, Any]],
    staging: str,
    before_ids: set[int],
) -> dict[int, list[dict[str, Any]]]:
    """把扫描到的文件归属到下载任务；无法确认的文件不参与本次整理。"""
    assigned: dict[int, list[dict[str, Any]]] = {int(entry["id"]): [] for entry in entries}
    used: set[int] = set()

    def season_episode_match(entry: dict[str, Any], item: dict[str, Any]) -> bool:
        return (
            entry.get("season") is not None
            and entry.get("episode") is not None
            and item.get("season") == entry.get("season")
            and item.get("episode") == entry.get("episode")
        )

    for item in items:
        item_id = int(item["id"])
        if item_id in used:
            continue
        path_lower = str(item.get("source_path") or "").lower()
        name_lower = str(item.get("file_name") or "").lower()
        fresh = item_id not in before_ids
        best: tuple[int, dict[str, Any]] | None = None
        for entry in entries:
            raw = _entry_raw(entry)
            task_name = str(raw.get("task_name") or "").lower().strip()
            title = _entry_title(entry)
            linked = entry.get("item_id")
            linked_id = int(linked) if linked not in (None, "") else 0
            score = 0
            # 上一轮已经记录过的「条目 → 文件」关系优先级最高，避免重跑时任务名丢失后无法归属。
            if linked_id and linked_id == item_id:
                score = 2000
            elif task_name and (
                name_lower == task_name or f"/{task_name}/" in path_lower
            ):
                score = 1000 + len(task_name)
            elif fresh and title and title in path_lower:
                score = 300 if season_episode_match(entry, item) else 100 + len(title)
            elif fresh and season_episode_match(entry, item):
                score = 200
            if score and (best is None or score > best[0]):
                best = (score, entry)
        if best is not None:
            assigned[int(best[1]["id"])].append(item)
            used.add(item_id)

    if len(entries) == 1 and str(entries[0].get("status") or "") == "saved":
        only = int(entries[0]["id"])
        for item in items:
            item_id = int(item["id"])
            if item_id in used or item_id in before_ids:
                continue
            if str(item.get("source_path") or "").lower().startswith(
                norm(staging).lower()
            ):
                assigned[only].append(item)
                used.add(item_id)
    return assigned


def _update_entry_outcomes(
    entries: list[dict[str, Any]],
    assigned: dict[int, list[dict[str, Any]]],
    duplicate_ids: set[int],
    stats: defaultdict[str, int],
) -> None:
    for entry in entries:
        items = assigned.get(int(entry["id"])) or []
        if not items:
            continue
        statuses = {str(item.get("status") or "") for item in items}
        target = next(
            (
                item.get("target_path")
                for item in items
                if item.get("target_path")
            ),
            None,
        )
        duplicate_error = next(
            (
                str(item.get("error") or "")
                for item in items
                if int(item["id"]) in duplicate_ids
            ),
            "",
        )
        if "organized" in statuses:
            _set_entry(
                entry["id"],
                status="organized",
                target_path=target,
                error=None,
                item_id=items[0]["id"],
            )
            entry["status"] = "organized"
        elif duplicate_error or int(items[0]["id"]) in duplicate_ids:
            _set_entry(
                entry["id"],
                status="duplicate",
                error=(duplicate_error or "已整理过同集")[:800],
            )
            entry["status"] = "duplicate"
        elif "review" in statuses:
            _set_entry(entry["id"], status="review", item_id=items[0]["id"])
            entry["status"] = "review"
        elif "unmatched" in statuses:
            _set_entry(entry["id"], status="unmatched", item_id=items[0]["id"])
            entry["status"] = "unmatched"
        elif "ignored" in statuses:
            _set_entry(entry["id"], status="ignored", item_id=items[0]["id"])
            entry["status"] = "ignored"
        elif "matched" in statuses:
            if not entry.get("item_id"):
                _set_entry(entry["id"], status="matched", item_id=items[0]["id"])
            entry["status"] = "matched"
        else:
            stats["failed"] += 1


def _feed_label(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}"[:200]


def run_rss_subscription(subscription: dict[str, Any], run_id: int) -> dict[str, Any]:
    hooks = RunHooks(run_id)
    notifier = RunNotifier(subscription)
    stats: defaultdict[str, int] = defaultdict(int)
    subscription_id = int(subscription["id"])
    started = time.monotonic()
    try:
        if not subscription.get("library_id"):
            raise SubscriptionError("订阅没有关联影视库")
        config = normalize_config(loads(subscription["config"], {}), subscription["name"])
        library = query_one("SELECT * FROM libraries WHERE id=?", (subscription["library_id"],))
        if not library:
            raise SubscriptionError("订阅关联的影视库已删除")
        source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
        if not source_row:
            raise SubscriptionError("影视库的文件源已删除")
        source = build_source(source_row)
        save_subdir = str(config.get("save_subdir") or "")
        if not config.get("save_root"):
            save_subdir = safe_save_subdir(save_subdir, subscription["name"])
        staging, cloud_parts = staging_paths(
            library["root_path"] or "",
            save_subdir,
            cloud_root=str(config.get("p115_root") or ""),
        )
        if not staging:
            raise SubscriptionError("影视库根目录为空，无法确定暂存目录")
        hooks.log(f"开始检查 RSS 订阅「{subscription['name']}」")
        hooks.log(f"暂存目录：{staging}")
        hooks.phase(0, 35)

        feed_items: list[FeedItem] = []
        feed_errors: list[str] = []
        for feed_url in config["feed_urls"]:
            if hooks.cancelled():
                break
            hooks.log(f"读取 RSS：{_feed_label(feed_url)}")
            result = fetch_feed(feed_url)
            stats["feeds"] += 1
            if result.error:
                stats["feed_errors"] += 1
                feed_errors.append(f"{_feed_label(feed_url)}: {result.error}")
                hooks.log(f"RSS 读取失败：{result.error}", "error")
                continue
            feed_items.extend(result.items)
            hooks.log(f"「{result.title or _feed_label(feed_url)}」共 {len(result.items)} 条")
        if not feed_items and feed_errors:
            message = "所有 RSS 源读取失败\n" + "\n".join(feed_errors[:10])
            hooks.log(message, "error")
            _notify(subscription, "run.failed", "RSS 读取失败", message)
            return {"status": "failed", "stats": dict(stats), "message": message}

        candidates: list[tuple[FeedItem, dict[str, Any]]] = []
        for item in feed_items:
            if not is_candidate(item, config):
                stats["filtered"] += 1
                continue
            entry = _upsert_entry(subscription_id, item)
            if entry.get("status") in RSS_TERMINAL:
                stats["skipped"] += 1
                continue
            candidates.append((item, entry))
        hooks.log(
            f"共 {len(feed_items)} 条，过滤后 {len(candidates)} 条待处理，"
            f"跳过已处理 {stats['skipped']} 条"
        )

        client = p115_client()
        try:
            folder_id = str(config.get("p115_folder_id") or "").strip()
            if folder_id:
                try:
                    client.list_entries(folder_id)
                except P115Error as exc:
                    hooks.log(f"115 目标目录不可用，改用路径定位：{exc}", "warn")
                    folder_id = ""
            if not folder_id:
                folder_id = client.ensure_folder_path(cloud_parts)
            hooks.log(
                f"115 保存目录：{config.get('p115_folder_path') or '/'.join(cloud_parts)}"
            )

            active = query_all(
                "SELECT * FROM subscription_entries WHERE subscription_id=? "
                "AND status IN ('queued','downloading') AND task_id<>'' ORDER BY id",
                (subscription_id,),
            )
            hooks.phase(5, 45)
            added = 0
            max_per_run = int(config.get("max_per_run") or 0)
            transfer_failed: list[str] = []
            for item, entry in candidates:
                if hooks.cancelled():
                    break
                if entry.get("status") not in {"pending", "failed"}:
                    continue
                if max_per_run > 0 and added >= max_per_run:
                    stats["deferred"] += 1
                    continue
                url, kind = item.download_url, item.download_kind
                try:
                    if kind == "page":
                        url, kind = resolve_page_download(item.link or url)
                    if kind == "torrent":
                        page_url = "" if item.download_kind == "page" else item.link
                        url, kind = resolve_torrent_magnet(url, page_url=page_url)
                except RssError as exc:
                    _set_entry(entry["id"], status="failed", error=str(exc)[:500])
                    stats["failed"] += 1
                    transfer_failed.append(f"{item.title}: {exc}")
                    hooks.log(f"解析下载地址失败：{item.title}: {exc}", "warn")
                    continue
                if kind not in DOWNLOAD_KINDS or not url:
                    _set_entry(
                        entry["id"],
                        status="unsupported",
                        error=f"暂不支持该下载类型: {kind or '未知'}",
                    )
                    stats["failed"] += 1
                    continue
                try:
                    task = client.add_task(url, folder_id=folder_id)
                except P115Error as exc:
                    message = str(exc)[:500]
                    _set_entry(entry["id"], status="failed", error=message)
                    stats["failed"] += 1
                    transfer_failed.append(f"{item.title}: {message}")
                    hooks.log(f"添加离线任务失败：{item.title}: {message}", "error")
                    continue
                _set_queued(entry, task, staging)
                active.append(entry)
                added += 1
                stats["added"] += 1
                hooks.log(f"已添加 115 离线任务：{item.title}")

            if transfer_failed:
                for text in transfer_failed:
                    notifier.add("transfer.failed", text)

            hooks.phase(50, 35)
            hooks.update(progress=50, message="等待 115 离线下载")
            completed: list[dict[str, Any]] = []
            task_failures: list[dict[str, Any]] = []
            if active:
                deadline = time.monotonic() + float(config.get("task_timeout") or 300)
                pending = {int(entry["id"]): entry for entry in active}
                while pending and not hooks.cancelled():
                    try:
                        task_list = client.list_tasks()
                        tasks = {str(task.get("id")): task for task in task_list}
                        by_token: dict[str, dict[str, Any]] = {}
                        for task in task_list:
                            for token in _transfer_tokens(
                                str(task.get("url") or ""),
                                str(task.get("info_hash") or ""),
                            ):
                                by_token.setdefault(token, task)
                    except P115Error as exc:
                        hooks.log(f"读取 115 任务失败：{exc}", "warn")
                        tasks, by_token = {}, {}
                    for entry_id, entry in list(pending.items()):
                        task = tasks.get(str(entry.get("task_id") or ""))
                        if not task:
                            raw = _entry_raw(entry)
                            for token in _transfer_tokens(
                                str(raw.get("task_url") or raw.get("download_url") or ""),
                                str(raw.get("task_info_hash") or ""),
                            ):
                                task = by_token.get(token)
                                if task:
                                    break
                            if task and str(entry.get("task_id") or "") != str(task.get("id")):
                                _set_entry(entry["id"], task_id=str(task.get("id")))
                                entry["task_id"] = str(task.get("id"))
                        if not task:
                            continue
                        state = _set_task_state(entry, task)
                        if state == "done":
                            completed.append(entry)
                            pending.pop(entry_id, None)
                            stats["downloaded"] += 1
                            hooks.log(f"115 下载完成：{entry['file_name']}")
                        elif state == "failed":
                            task_failures.append(entry)
                            pending.pop(entry_id, None)
                            stats["failed"] += 1
                            hooks.log(
                                f"115 离线任务失败：{entry['file_name']}", "error"
                            )
                    if not pending or time.monotonic() >= deadline:
                        break
                    time.sleep(3.0)
                stats["waiting_tasks"] = len(pending)
                if pending:
                    hooks.log(
                        f"仍有 {len(pending)} 个离线任务未完成，下次运行继续检查", "warn"
                    )

            for entry in completed:
                notifier.add("transfer.success", entry["file_name"])
            for entry in task_failures:
                notifier.add(
                    "transfer.failed",
                    f"{entry['file_name']}: {entry.get('error') or ''}",
                )

            scannable = query_all(
                "SELECT * FROM subscription_entries WHERE subscription_id=? "
                "AND status IN ('saved','matched','review','unmatched','ignored') "
                "AND saved_path IS NOT NULL AND saved_path<>'' ORDER BY id",
                (subscription_id,),
            )
            if not scannable:
                message = _summary(stats, feed_errors)
                hooks.update(progress=100, message=message)
                notifier.flush(message, dict(stats), force=bool(feed_errors))
                status = "partial" if stats["failed"] or stats["feed_errors"] else "success"
                return {"status": status, "stats": dict(stats), "message": message}

            hooks.phase(85, 5)
            hooks.update(progress=85, message="扫描暂存目录")
            refresh_error = ""
            try:
                if refresh_directory(source_row, staging):
                    hooks.log(f"已刷新 OpenList 目录缓存：{staging}", "debug")
            except OpenListError as exc:
                refresh_error = str(exc)
                hooks.log(f"OpenList 刷新目录失败：{exc}", "warn")
            before_ids = {
                int(row["id"])
                for row in query_all(
                    "SELECT id, source_path FROM items WHERE library_id=?",
                    (library["id"],),
                )
                if str(row.get("source_path") or "").startswith(staging + "/")
            }
            scan_result = scan_library(
                library["id"],
                hooks,
                root_path=staging,
                scope=staging,
                mark_missing=False,
                subscription_id=subscription_id,
            )
            item_ids = [item["id"] for item in scan_result.get("items", [])]
            full_items: list[dict[str, Any]] = []
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                full_items = query_all(
                    f"SELECT * FROM items WHERE id IN ({placeholders})", tuple(item_ids)
                )
            assigned = _attribute_items(scannable, full_items, staging, before_ids)
            hooks.log(
                "文件归属：" + "，".join(
                    f"{entry['file_name']} → {len(assigned.get(int(entry['id']), []))} 个"
                    for entry in scannable
                    if assigned.get(int(entry["id"]))
                )
            )

            matched_pairs: list[tuple[dict[str, Any], None]] = []
            matched_ids: set[int] = set()
            for entry in scannable:
                for item in assigned.get(int(entry["id"]), []):
                    status = str(item.get("status") or "")
                    if status == "matched":
                        matched_pairs.append((item, None))
                        matched_ids.add(int(item["id"]))
                    elif status == "review":
                        stats["review"] += 1
                        if entry.get("status") != "review":
                            notifier.add("item.review", item["file_name"])
                    elif status == "unmatched":
                        stats["unmatched"] += 1
                        if entry.get("status") != "unmatched":
                            notifier.add("item.unmatched", item["file_name"])
                    elif status == "ignored":
                        stats["ignored"] += 1

            duplicates = _plan_duplicates(
                library, subscription, matched_pairs, hooks, stats, notifier
            )
            queued_ids = {int(plan["item"]["id"]) for plan in duplicates}
            duplicate_ids = matched_ids - queued_ids
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
            )

            if assigned:
                all_item_ids = [
                    int(item["id"])
                    for items in assigned.values()
                    for item in items
                ]
                placeholders = ",".join("?" for _ in all_item_ids)
                final_items = {
                    int(row["id"]): row
                    for row in query_all(
                        f"SELECT * FROM items WHERE id IN ({placeholders})",
                        tuple(all_item_ids),
                    )
                }
                for _entry_id, items in assigned.items():
                    for index, item in enumerate(items):
                        if int(item["id"]) in final_items:
                            items[index] = final_items[int(item["id"])]
                _update_entry_outcomes(scannable, assigned, duplicate_ids, stats)

            message = _summary(stats, feed_errors, refresh_error)
            hooks.update(progress=100, message=message)
            notifier.flush(
                message,
                dict(stats),
                force=bool(feed_errors or refresh_error),
            )
            status = (
                "partial"
                if stats["failed"] or stats["feed_errors"] or refresh_error
                else "success"
            )
            return {"status": status, "stats": dict(stats), "message": message}
        finally:
            client.close()
    except RssError as exc:
        message = f"RSS 配置错误: {exc}"
        hooks.log(message, "error")
        _notify(subscription, "run.failed", "运行失败", message)
        raise SubscriptionError(message) from exc
    except P115Error as exc:
        message = f"115 接口错误: {exc}"
        hooks.log(message, "error")
        _notify(subscription, "run.failed", "运行失败", message)
        raise SubscriptionError(message) from exc
    except Exception as exc:
        _notify(
            subscription,
            "run.failed",
            "运行失败",
            f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        stats["elapsed"] = int(time.monotonic() - started)


def _summary(
    stats: defaultdict[str, int], feed_errors: list[str], refresh_error: str = ""
) -> str:
    parts = [
        f"RSS {stats['feeds']} 个",
        f"新增任务 {stats['added']}",
        f"下载完成 {stats['downloaded']}",
        f"整理 {stats['organized']}",
        f"替换 {stats['replaced']}",
        f"未识别 {stats['unmatched']}",
        f"待确认 {stats['review']}",
        f"重复 {stats['duplicate']}",
        f"失败 {stats['failed']}",
        f"跳过 {stats['skipped']}",
    ]
    if stats["waiting_tasks"]:
        parts.append(f"等待中 {stats['waiting_tasks']}")
    if feed_errors:
        parts.append(f"RSS 异常 {len(feed_errors)}")
    if refresh_error:
        parts.append("目录刷新失败")
    text = "，".join(parts)
    if feed_errors:
        text += "\n" + "\n".join(feed_errors[:10])
    if refresh_error:
        text += f"\nOpenList 刷新失败：{refresh_error}"
    return text
