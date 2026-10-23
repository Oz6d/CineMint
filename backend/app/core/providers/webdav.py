from __future__ import annotations

import posixpath
from typing import Any
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree

import httpx

from .base import Entry, FileSource, SourceError, SourceNotFound, norm, safe_relative

DAV = "{DAV:}"

# 403 常见于 OpenList / AList 未开启写入类权限
PERMISSION_HINT = "（OpenList/AList 需在后台「用户 → 编辑 → 权限」中开启“WebDAV 管理”）"


def _status_suffix(status: int) -> str:
    return PERMISSION_HINT if status == 403 else ""


def _join_url(base: str, path: str) -> str:
    quoted = quote(safe_relative(path), safe="/~!$&'()*+,;=:@")
    if not quoted:
        return base
    return base.rstrip("/") + "/" + quoted


class WebDavFileSource(FileSource):
    type = "webdav"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        raw_url = (self.config.get("url") or "").strip()
        if not raw_url:
            raise SourceError("缺少 WebDAV 地址")
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"}:
            raise SourceError("WebDAV 地址必须以 http(s):// 开头")
        self.base_url = raw_url.rstrip("/")
        self.base_path = unquote(parsed.path).rstrip("/")
        self.username = self.config.get("username") or ""
        self.password = self.config.get("password") or ""
        self.client = httpx.Client(
            timeout=httpx.Timeout(30.0, read=120.0),
            verify=bool(self.config.get("verify_tls", True)),
            auth=(self.username, self.password) if self.username else None,
            follow_redirects=True,
            headers={"User-Agent": "CineFlow/0.1"},
        )

    def _url(self, path: str) -> str:
        return _join_url(self.base_url, path)

    def _relative(self, href: str) -> str:
        parsed = urlparse(href)
        path = unquote(parsed.path)
        base_path = self.base_path
        if base_path and path.startswith(base_path):
            path = path[len(base_path):]
        return norm(path)

    def _dav_hint(self) -> str:
        """裸地址（未填路径）时提示 OpenList / AList 需要 /dav 后缀。"""
        if self.base_path:
            return ""
        return "（提示：OpenList/AList 的地址需要以 /dav 结尾，例如 http://192.168.1.10:5244/dav）"

    def _propfind(self, url: str, depth: str = "1") -> httpx.Response:
        try:
            return self.client.request(
                "PROPFIND",
                url,
                headers={"Depth": depth, "Content-Type": "application/xml"},
                content=b'<?xml version="1.0"?><propfind xmlns="DAV:"><prop>'
                b"<resourcetype/><getcontentlength/><getlastmodified/></prop></propfind>",
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"WebDAV 连接失败: {exc}") from exc

    def list(self, path: str = "") -> list[Entry]:
        url = self._url(path)
        response = self._propfind(url, depth="1")
        if response.status_code == 404:
            raise SourceNotFound(f"WebDAV 目录不存在: {path or '/'}{self._dav_hint()}")
        if response.status_code in (401, 403):
            if not self.username or not self.password:
                raise SourceError("WebDAV 认证失败：用户名或密码为空，请先在文件源中补全凭据")
            raise SourceError("WebDAV 认证失败，请检查用户名或密码")
        if response.status_code not in (200, 207):
            raise SourceError(f"WebDAV 返回异常状态: {response.status_code}{self._dav_hint()}")
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            raise SourceError(f"WebDAV PROPFIND 响应无法解析{self._dav_hint()}") from exc

        wanted = norm(path)
        entries: list[Entry] = []
        for node in root.findall(f"{DAV}response"):
            href_node = node.find(f"{DAV}href")
            if href_node is None or not href_node.text:
                continue
            rel = self._relative(href_node.text)
            if rel == wanted:
                continue
            if wanted and not rel.startswith(wanted.rstrip("/") + "/"):
                continue
            name = rel.rsplit("/", 1)[-1]
            if not name:
                continue
            is_dir = False
            size = 0
            mtime = 0.0
            for propstat in node.findall(f"{DAV}propstat"):
                status = propstat.findtext(f"{DAV}status") or ""
                if " 200 " not in status and not status.endswith(" 200"):
                    continue
                prop = propstat.find(f"{DAV}prop")
                if prop is None:
                    continue
                if prop.find(f"{DAV}resourcetype/{DAV}collection") is not None:
                    is_dir = True
                length = prop.findtext(f"{DAV}getcontentlength")
                if length and length.isdigit():
                    size = int(length)
                modified = prop.findtext(f"{DAV}getlastmodified")
                if modified:
                    from email.utils import parsedate_to_datetime

                    try:
                        mtime = parsedate_to_datetime(modified).timestamp()
                    except (TypeError, ValueError):
                        mtime = 0.0
            entries.append(Entry(name=name, path=rel, is_dir=is_dir, size=size, mtime=mtime))
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.lower()))

    def list_dirs(self, path: str = "") -> list[Entry]:
        return super().list_dirs(path)

    def exists(self, path: str) -> bool:
        try:
            response = self._propfind(self._url(path), depth="0")
        except SourceError:
            return False
        return response.status_code in (200, 207)

    def stat(self, path: str) -> Entry | None:
        parent = posixpath.dirname(norm(path))
        name = norm(path).rsplit("/", 1)[-1]
        try:
            for entry in self.list(parent):
                if entry.name == name:
                    return entry
        except SourceError:
            return None
        return None

    def mkdir(self, path: str) -> None:
        rel = safe_relative(path)
        if not rel:
            return
        parts = rel.split("/")
        current: list[str] = []
        for part in parts:
            current.append(part)
            target = "/".join(current)
            if self.exists(target):
                continue
            response = self.client.request("MKCOL", self._url(target))
            if response.status_code not in (201, 200, 405):
                raise SourceError(
                    f"创建目录失败 {target}: HTTP {response.status_code}{_status_suffix(response.status_code)}"
                )

    def read(self, path: str) -> bytes:
        response = self.client.get(self._url(path))
        if response.status_code != 200:
            raise SourceError(f"读取失败 {path}: HTTP {response.status_code}")
        return response.content

    def write(self, path: str, data: bytes) -> None:
        parent = posixpath.dirname(norm(path))
        if parent:
            self.mkdir(parent)
        response = self.client.put(self._url(path), content=data)
        if response.status_code not in (200, 201, 204):
            raise SourceError(
                f"写入失败 {path}: HTTP {response.status_code}{_status_suffix(response.status_code)}"
            )

    def _move_once(self, source: str, target: str) -> None:
        headers = {
            "Destination": self._url(target),
            "Overwrite": "F",
            "Depth": "infinity",
        }
        try:
            response = self.client.request("MOVE", self._url(source), headers=headers)
        except httpx.HTTPError as exc:
            raise SourceError(f"移动失败 {source}: {exc}") from exc
        if response.status_code in (201, 204):
            return
        if response.status_code in (405, 501):
            # 部分 WebDAV 服务不支持 MOVE，降级为读+写+删
            if self.exists(target):
                raise SourceError(f"目标已存在: {target}")
            data = self.read(source)
            self.write(target, data)
            self.delete(source)
            return
        raise SourceError(
            f"移动失败 {source}: HTTP {response.status_code}{_status_suffix(response.status_code)}"
        )

    def move(self, src: str, dst: str) -> None:
        source, target = safe_relative(src), safe_relative(dst)
        if source == target:
            return
        parent = posixpath.dirname(target)
        if parent:
            self.mkdir(parent)

        source_dir = posixpath.dirname(source)
        source_name = posixpath.basename(source)
        target_name = posixpath.basename(target)
        staged = source
        if source_name != target_name:
            # 先在同目录改名，再跨目录移动。OpenList + 夸克等驱动跨目录 MOVE 时
            # 会先移动、紧接着改名；第二步在网盘侧可能瞬时失败并返回 500。
            # 目标名提前改好后，服务端移动时无需再改名。
            staged = posixpath.join(source_dir, target_name) if source_dir else target_name
            try:
                self._move_once(source, staged)
            except SourceError:
                source_present = self.exists(source)
                # 上一次尝试已完成改名：源文件已不在，目标目录里是改名后的文件
                if self.exists(staged) and not source_present:
                    pass
                # 上一次尝试已全部完成
                elif self.exists(target) and not source_present:
                    return
                else:
                    raise

        try:
            self._move_once(staged, target)
        except SourceError as exc:
            # 服务端可能已完成移动但返回了错误状态
            if not self.exists(staged) and self.exists(target):
                return
            if staged != source:
                # 尽量恢复源文件名，方便用户直接重试
                try:
                    self._move_once(staged, source)
                except SourceError:
                    raise SourceError(
                        f"{exc}；源文件已改名到 {staged}，重新整理可继续"
                    ) from exc
            raise

    def delete(self, path: str) -> None:
        response = self.client.delete(self._url(path))
        if response.status_code not in (200, 204, 404):
            raise SourceError(
                f"删除失败 {path}: HTTP {response.status_code}{_status_suffix(response.status_code)}"
            )

    def test(self) -> dict[str, Any]:
        candidates = [self.base_url]
        if not self.base_path:
            # OpenList / AList 的 WebDAV 端点固定在 /dav：裸地址自动探测并补全
            candidates.append(self.base_url + "/dav")
        failure = "连接失败"
        for index, url in enumerate(candidates):
            response = self._propfind(url, depth="1")
            if response.status_code in (401, 403):
                if not self.username or not self.password:
                    raise SourceError("认证失败：用户名或密码为空，请先填写 OpenList/WebDAV 账号密码")
                raise SourceError("认证失败，请检查用户名或密码")
            if response.status_code == 429:
                failure = "服务返回 HTTP 429（请求过于频繁，OpenList 连续登录失败后会限流，请稍等几分钟再试）"
                continue
            if response.status_code not in (200, 207):
                failure = f"服务返回 HTTP {response.status_code}"
                continue
            try:
                root = ElementTree.fromstring(response.content)
            except ElementTree.ParseError:
                failure = "该地址返回的不是 WebDAV 响应（可能是网页地址）"
                continue
            if root.tag != f"{DAV}multistatus":
                failure = "该地址返回的不是 WebDAV 响应（可能是网页地址）"
                continue
            result: dict[str, Any] = {
                "ok": True,
                "server": response.headers.get("server", "webdav"),
                "entries": max(len(root.findall(f"{DAV}response")) - 1, 0),
            }
            if index > 0:
                result["normalized_url"] = url
            return result
        raise SourceError(f"{failure}{self._dav_hint()}")
