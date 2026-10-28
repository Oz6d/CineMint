"""RSS / Atom 解析与订阅条目规范化。

只负责把订阅源转换成统一的下载条目，不访问 115，也不做文件匹配。
"""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx

from .cloud_paths import safe_save_subdir
from .parser import file_ext, parse_media_name

USER_AGENT = "CineMint/0.1 (+self-hosted media organizer)"
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_TORRENT_BYTES = 4 * 1024 * 1024
MAGNET_RE = re.compile(r"magnet:\?[^\s\"'<>]+", re.I)
ED2K_RE = re.compile(r"ed2k://[^\s\"'<>]+", re.I)
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv", ".webm"}


class RssError(RuntimeError):
    pass


@dataclass
class FeedItem:
    feed_url: str
    guid: str
    title: str
    link: str
    download_url: str
    download_kind: str
    published_at: str = ""
    size: int = 0
    summary: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def remote_key(self) -> str:
        seed = f"{self.feed_url}\n{self.guid or self.download_url or self.link}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()


@dataclass
class FeedResult:
    url: str
    title: str
    items: list[FeedItem]
    error: str = ""
    image: str = ""


def normalize_feed_urls(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        parts = [str(value or "") for value in raw]
    else:
        parts = re.split(r"[\n,，;；]+", str(raw or ""))
    result: list[str] = []
    for part in parts:
        value = part.strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RssError(f"RSS 地址无效: {value[:200]}")
        if value not in result:
            result.append(value[:1000])
    if len(result) > 20:
        raise RssError("单个订阅最多填写 20 个 RSS 地址")
    return result


def _local(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _child(element: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    wanted = {name.lower() for name in names}
    for child in element:
        if _local(child.tag) in wanted:
            return child
    return None


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in element if _local(child.tag) == name.lower()]


def _parse_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    for parser in (
        lambda raw: parsedate_to_datetime(raw),
        lambda raw: datetime.fromisoformat(raw.replace("Z", "+00:00")),
    ):
        try:
            parsed = parser(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    return ""


def _atom_link(entry: ElementTree.Element) -> tuple[str, str, str]:
    alternate = ""
    enclosure = ""
    enclosure_type = ""
    for link in _children(entry, "link"):
        href = str(link.get("href") or "").strip()
        if not href:
            continue
        rel = str(link.get("rel") or "alternate").lower()
        if rel == "enclosure":
            enclosure = href
            enclosure_type = str(link.get("type") or "")
        elif rel in {"alternate", ""} and not alternate:
            alternate = href
    return alternate, enclosure, enclosure_type


def _pick_download(
    link: str,
    enclosure: str,
    enclosure_type: str,
    summary: str,
) -> tuple[str, str]:
    for candidate in (enclosure, link):
        lowered = candidate.lower()
        if lowered.startswith("magnet:"):
            return candidate, "magnet"
        if lowered.startswith("ed2k://"):
            return candidate, "ed2k"
    for pattern, kind in ((MAGNET_RE, "magnet"), (ED2K_RE, "ed2k")):
        match = pattern.search(summary or "")
        if match:
            return match.group(0), kind
    for candidate in (enclosure, link):
        if candidate.lower().startswith(("http://", "https://")):
            if file_ext(urlparse(candidate).path.lower()) in VIDEO_EXTS:
                return candidate, "http"
    if enclosure.lower().startswith(("http://", "https://")) and (
        urlparse(enclosure).path.lower().endswith(".torrent")
        or "bittorrent" in enclosure_type.lower()
    ):
        return enclosure, "torrent"
    if link.lower().startswith(("http://", "https://")):
        if urlparse(link).path.lower().endswith(".torrent"):
            return link, "torrent"
        return link, "page"
    return "", ""


def _item_from_rss(
    element: ElementTree.Element, feed_url: str
) -> FeedItem | None:
    title = _text(_child(element, "title"))
    link = _text(_child(element, "link"))
    guid = _text(_child(element, "guid", "id"))
    summary = _text(
        _child(element, "description", "summary", "encoded", "content")
    )
    enclosure = _child(element, "enclosure")
    enclosure_url = (
        str(enclosure.get("url") or "").strip() if enclosure is not None else ""
    )
    enclosure_type = (
        str(enclosure.get("type") or "") if enclosure is not None else ""
    )
    media = _child(element, "content", "thumbnail")
    if not enclosure_url and media is not None:
        media_url = str(media.get("url") or "").strip()
        if media_url:
            enclosure_url = media_url
            enclosure_type = str(media.get("type") or "") or enclosure_type
    published = _text(_child(element, "pubdate", "published", "updated", "date"))
    download_url, download_kind = _pick_download(
        link, enclosure_url, enclosure_type, summary
    )
    if not title and not download_url:
        return None
    size = 0
    length = (
        str(enclosure.get("length") or "") if enclosure is not None else ""
    )
    if length.isdigit():
        size = int(length)
    raw = {
        "guid": guid,
        "link": link,
        "download_url": download_url,
        "download_kind": download_kind,
        "size": size,
        "published_at": _parse_date(published),
    }
    return FeedItem(
        feed_url=feed_url,
        guid=guid or link or download_url,
        title=title or download_url,
        link=link,
        download_url=download_url,
        download_kind=download_kind,
        published_at=raw["published_at"],
        size=size,
        summary=summary[:2000],
        raw=raw,
    )


def _item_from_atom(
    entry: ElementTree.Element, feed_url: str
) -> FeedItem | None:
    title = _text(_child(entry, "title"))
    link, enclosure, enclosure_type = _atom_link(entry)
    guid = _text(_child(entry, "id")) or link
    summary = _text(_child(entry, "summary", "content"))
    published = _text(_child(entry, "published", "updated"))
    download_url, download_kind = _pick_download(
        link, enclosure, enclosure_type, summary
    )
    if not title and not download_url:
        return None
    raw = {
        "guid": guid,
        "link": link,
        "download_url": download_url,
        "download_kind": download_kind,
        "size": 0,
        "published_at": _parse_date(published),
    }
    return FeedItem(
        feed_url=feed_url,
        guid=guid,
        title=title or download_url,
        link=link,
        download_url=download_url,
        download_kind=download_kind,
        published_at=raw["published_at"],
        summary=summary[:2000],
        raw=raw,
    )


def parse_feed(content: bytes, feed_url: str) -> FeedResult:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise RssError(f"RSS 解析失败: {exc}") from exc
    root_name = _local(root.tag)
    image = ""
    if root_name == "rss":
        channel = _child(root, "channel")
        if channel is None:
            raise RssError("RSS 缺少 channel")
        title = _text(_child(channel, "title"))
        image_node = _child(channel, "image")
        if image_node is not None:
            image = _text(_child(image_node, "url"))
        items = [
            item
            for element in _children(channel, "item")
            if (item := _item_from_rss(element, feed_url)) is not None
        ]
    elif root_name == "feed":
        title = _text(_child(root, "title"))
        image = _text(_child(root, "logo", "icon"))
        items = [
            item
            for element in _children(root, "entry")
            if (item := _item_from_atom(element, feed_url)) is not None
        ]
    elif root_name == "rdf":
        title = _text(_child(root, "title"))
        image_node = _child(root, "image")
        if image_node is not None:
            image = _text(_child(image_node, "url")) or str(
                image_node.get("resource") or ""
            )
        items = [
            item
            for element in _children(root, "item")
            if (item := _item_from_rss(element, feed_url)) is not None
        ]
    else:
        raise RssError(f"不支持的订阅格式: {root_name}")
    return FeedResult(url=feed_url, title=title, items=items, image=image)


def fetch_feed(url: str, *, timeout: float = 20.0) -> FeedResult:
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return FeedResult(url=url, title="", items=[], error=f"请求失败: {type(exc).__name__}")
    if response.status_code >= 400:
        return FeedResult(
            url=url, title="", items=[], error=f"HTTP {response.status_code}"
        )
    if len(response.content) > MAX_FEED_BYTES:
        return FeedResult(url=url, title="", items=[], error="RSS 内容超过 8 MB")
    try:
        return parse_feed(response.content, url)
    except RssError as exc:
        return FeedResult(url=url, title="", items=[], error=str(exc))


def _page_links(markup: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(
        r"""(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')""", markup, re.I
    ):
        value = html_lib.unescape((match.group(1) or match.group(2) or "").strip())
        if value:
            links.append(value)
    links.extend(html_lib.unescape(match.group(0)) for match in MAGNET_RE.finditer(markup))
    links.extend(html_lib.unescape(match.group(0)) for match in ED2K_RE.finditer(markup))
    return links


def resolve_page_download(url: str, *, timeout: float = 20.0) -> tuple[str, str]:
    """从详情页提取 magnet / ed2k / .torrent 下载地址。"""
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html, */*"},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise RssError(f"详情页请求失败: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise RssError(f"详情页 HTTP {response.status_code}")
    if len(response.content) > 2 * 1024 * 1024:
        raise RssError("详情页内容超过 2 MB")
    for link in _page_links(response.text):
        lowered = link.lower()
        if lowered.startswith("magnet:"):
            return link, "magnet"
        if lowered.startswith("ed2k://"):
            return link, "ed2k"
        absolute = urljoin(str(response.url), link)
        if urlparse(absolute).path.lower().endswith(".torrent"):
            return absolute, "torrent"
    raise RssError("详情页中没有找到磁力、电驴或种子链接")


def normalize_config(raw: Any, name: str) -> dict[str, Any]:
    data = dict(raw or {})
    feed_urls = normalize_feed_urls(data.get("feed_urls") or data.get("feed_url") or "")
    if not feed_urls:
        raise RssError("请填写至少一个 RSS 地址")
    keyword = str(data.get("keyword") or "").strip()[:1000]
    pattern = str(data.get("regex") or "").strip()[:2000]
    if pattern:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RssError(f"正则表达式无效: {exc}") from exc
    raw_max = data.get("max_per_run")
    try:
        max_per_run = 20 if raw_max in (None, "") else int(raw_max)
    except (TypeError, ValueError):
        max_per_run = 20
    max_per_run = max(0, min(max_per_run, 200))
    try:
        visibility = int(data.get("visibility_timeout") or 120)
    except (TypeError, ValueError):
        visibility = 120
    visibility = max(10, min(visibility, 900))
    try:
        task_timeout = int(data.get("task_timeout") or 300)
    except (TypeError, ValueError):
        task_timeout = 300
    task_timeout = max(10, min(task_timeout, 3600))
    try:
        min_size_mb = float(data.get("min_size_mb") or 0)
    except (TypeError, ValueError):
        min_size_mb = 0.0
    min_size_mb = max(0.0, min(min_size_mb, 1024.0 * 100))
    save_subdir = str(
        data.get("save_subdir") or data.get("save_dir") or ""
    ).strip().strip("/")[:500]
    if save_subdir.startswith("..") or "/../" in f"/{save_subdir}/":
        raise RssError("暂存目录不能包含 ..")
    save_root = bool(data.get("save_root"))
    save_subdir = "" if save_root else safe_save_subdir(save_subdir, name)
    p115_root = str(data.get("p115_root") or "").strip().strip("/")[:500]
    if p115_root.startswith("..") or "/../" in f"/{p115_root}/":
        raise RssError("115 根目录不能包含 ..")
    folder_id = str(data.get("p115_folder_id") or "").strip()[:128]
    folder_path = str(data.get("p115_folder_path") or "").strip().strip("/")[:1000]
    if folder_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", folder_id):
        raise RssError("115 目录 ID 无效")
    if folder_path.startswith("..") or "/../" in f"/{folder_path}/":
        raise RssError("115 目标目录不能包含 ..")
    if not folder_id:
        folder_path = ""
    collision = str(data.get("collision") or "").strip().lower()
    if collision and collision not in ("suffix", "skip", "overwrite"):
        raise RssError("同名文件处理方式无效")
    return {
        "feed_urls": feed_urls,
        "keyword": keyword,
        "regex": pattern,
        "max_per_run": max_per_run,
        "visibility_timeout": visibility,
        "task_timeout": task_timeout,
        "min_size_mb": min_size_mb,
        "save_subdir": save_subdir,
        "save_root": save_root,
        "p115_root": p115_root,
        "p115_folder_id": folder_id,
        "p115_folder_path": folder_path,
        "collision": collision,
    }


def is_candidate(item: FeedItem, config: dict[str, Any]) -> bool:
    if not item.download_url or item.download_kind not in {
        "magnet", "torrent", "ed2k", "http", "page"
    }:
        return False
    keyword = str(config.get("keyword") or "")
    if keyword:
        terms = [
            term.strip()
            for term in re.split(r"[\n,，;；]+", keyword)
            if term.strip()
        ]
        lowered = item.title.lower()
        if terms and not all(term.lower() in lowered for term in terms):
            return False
    pattern = str(config.get("regex") or "")
    if pattern and not re.search(pattern, item.title):
        return False
    min_size_mb = float(config.get("min_size_mb") or 0)
    if min_size_mb and item.size and item.size < min_size_mb * 1024 * 1024:
        return False
    return True


def preview_item(item: FeedItem) -> dict[str, Any]:
    parsed = parse_media_name(item.title)
    return {
        "guid": item.guid,
        "title": item.title,
        "link": item.link,
        "download_url": item.download_url,
        "download_kind": item.download_kind,
        "published_at": item.published_at,
        "size": item.size,
        "parsed_title": parsed.title,
        "season": parsed.season,
        "episode": parsed.episode,
        "version": parsed.version,
        "version_label": parsed.version_label,
    }


def _bencode_end(data: bytes, pos: int) -> int:
    """返回从 pos 开始的一个 bencode 值的结束下标。"""
    marker = data[pos : pos + 1]
    if marker == b"i":
        end = data.find(b"e", pos)
        if end < 0:
            raise ValueError("bencode 整数未结束")
        return end + 1
    if marker in {b"l", b"d"}:
        pos += 1
        while data[pos : pos + 1] != b"e":
            if not data[pos : pos + 1]:
                raise ValueError("bencode 数据不完整")
            pos = _bencode_end(data, pos)
            if marker == b"d":
                pos = _bencode_end(data, pos)
        return pos + 1
    colon = data.find(b":", pos)
    if colon < 0 or not data[pos:colon].isdigit():
        raise ValueError("bencode 数据格式异常")
    end = colon + 1 + int(data[pos:colon])
    if end > len(data):
        raise ValueError("bencode 字符串越界")
    return end


def torrent_info_hash(content: bytes) -> str:
    """读取 .torrent 里 info 字典的 SHA-1，即 BTIH。"""
    if not content.startswith(b"d"):
        return ""
    try:
        pos = 1
        while pos < len(content) and content[pos : pos + 1] != b"e":
            key_start = pos
            pos = _bencode_end(content, pos)
            key = content[key_start:pos]
            value_start = pos
            pos = _bencode_end(content, pos)
            if key == b"4:info":
                return hashlib.sha1(content[value_start:pos]).hexdigest()
    except (ValueError, IndexError):
        return ""
    return ""


def fetch_torrent(url: str, *, timeout: float = 20.0) -> bytes:
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/x-bittorrent, */*"},
        ) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise RssError(f"种子下载失败: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise RssError(f"种子下载失败: HTTP {response.status_code}")
    content = response.content
    if not content or len(content) > MAX_TORRENT_BYTES:
        raise RssError("种子文件大小异常")
    return content


def resolve_torrent_magnet(
    url: str, *, page_url: str = "", timeout: float = 20.0
) -> tuple[str, str]:
    """把种子地址转成磁力：优先用详情页自带的磁力，否则读取种子算 BTIH。"""
    page = (page_url or "").strip()
    if page and page != url and page.lower().startswith(("http://", "https://")):
        try:
            page_url_resolved, page_kind = resolve_page_download(page, timeout=timeout)
        except RssError:
            page_url_resolved, page_kind = "", ""
        if page_kind == "magnet" and page_url_resolved:
            return page_url_resolved, "magnet"
        if page_kind == "torrent" and page_url_resolved:
            url = page_url_resolved
    info_hash = torrent_info_hash(fetch_torrent(url, timeout=timeout))
    if not info_hash:
        raise RssError("种子解析失败，无法生成磁力链接")
    return f"magnet:?xt=urn:btih:{info_hash}", "magnet"
