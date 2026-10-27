"""RSS / Atom 解析、过滤与订阅配置测试。

运行：cd backend && .venv/bin/python ../tests/test_rss.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ["CINEFLOW_DATA_DIR"] = tempfile.mkdtemp(prefix="cineflow-rss-test-")
os.environ["CINEFLOW_DEMO"] = "false"

from app.core.rss import (  # noqa: E402
    RssError,
    fetch_feed,
    is_candidate,
    normalize_config,
    normalize_feed_urls,
    parse_feed,
    resolve_page_download,
    resolve_torrent_magnet,
    torrent_info_hash,
)


MIKAN_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Mikan Project</title>
    <item>
      <title>[DKB] Dragon Ball Super Beerus - S01E02 [1080p][HEVC x265 10bit].mkv</title>
      <link>https://mikanani.me/Home/Episode/abc</link>
      <guid isPermaLink="false">mikan-1</guid>
      <enclosure url="https://mikanani.me/Download/2026/abc.torrent"
                 length="123456789" type="application/x-bittorrent" />
      <pubDate>Mon, 26 Oct 2026 03:00:00 +0000</pubDate>
      <description>1080p subtitle</description>
    </item>
    <item>
      <title>[DKB] Dragon Ball Super Beerus - S01E01 [1080p][V2].mkv</title>
      <link>https://mikanani.me/Home/Episode/def</link>
      <guid isPermaLink="false">mikan-2</guid>
      <enclosure url="https://mikanani.me/Download/2026/def.torrent"
                 length="223456789" type="application/x-bittorrent" />
      <pubDate>Sun, 25 Oct 2026 03:00:00 +0000</pubDate>
    </item>
    <item>
      <title>[DKB] Dragon Ball Super Beerus - S01E03 [720p].mkv</title>
      <link>https://mikanani.me/Home/Episode/ghi</link>
      <guid isPermaLink="false">mikan-3</guid>
      <enclosure url="https://mikanani.me/Download/2026/ghi.torrent"
                 length="1024" type="application/x-bittorrent" />
    </item>
  </channel>
</rss>
"""

ATOM_FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Anime</title>
  <entry>
    <title>[Group] Test Show - 03 [1080p].mkv</title>
    <id>tag:example.com,2026:3</id>
    <link rel="alternate" href="https://example.com/post/3" />
    <link rel="enclosure" href="https://example.com/3.torrent"
          type="application/x-bittorrent" length="999" />
    <updated>2026-10-25T12:00:00Z</updated>
    <summary>episode three</summary>
  </entry>
</feed>
"""

MAGNET_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Magnet Feed</title>
    <item>
      <title>Magnet Show S02E04 1080p</title>
      <link>https://example.com/post/4</link>
      <guid>magnet-4</guid>
      <description><![CDATA[
        <a href="magnet:?xt=urn:btih:abcdef0123456789&dn=Magnet.Show.S02E04">download</a>
      ]]></description>
    </item>
  </channel>
</rss>
"""

RDF_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>RDF Feed</title>
  </channel>
  <item>
    <title>RDF Show S01E05</title>
    <link>magnet:?xt=urn:btih:rdf&amp;dn=RDF.Show.S01E05</link>
    <dc:date>2026-10-24T10:00:00+08:00</dc:date>
  </item>
</rdf:RDF>
"""


PRIORITY_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Priority Feed</title>
    <item>
      <title>Priority Show S01E01 1080p</title>
      <link>https://example.com/post/1</link>
      <guid>priority-1</guid>
      <enclosure url="https://example.com/1.torrent" type="application/x-bittorrent" />
      <description>magnet:?xt=urn:btih:summary&amp;dn=Priority.Show</description>
    </item>
  </channel>
</rss>
"""


def _bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(
            _bencode(key.encode() if isinstance(key, str) else key)
            + _bencode(value[key])
            for key in sorted(value)
        ) + b"e"
    raise TypeError(type(value))


TORRENT_INFO = {
    "name": b"Show.S01E01.1080p.mkv",
    "length": 147817760,
    "piece length": 262144,
    "pieces": b"0" * 20,
}
TORRENT_BYTES = _bencode(
    {"announce": b"http://tracker.example/announce", "info": TORRENT_INFO}
)
TORRENT_HASH = hashlib.sha1(_bencode(TORRENT_INFO)).hexdigest()


class _FeedHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/feed.xml":
            body = MIKAN_RSS.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
        elif self.path == "/garbage":
            body = b"<not-xml"
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
        elif self.path == "/page":
            body = (
                b'<html><body><a href="magnet:?xt=urn:btih:page&amp;dn=Page.Show">'
                b"magnet</a></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        elif self.path == "/torrent-page":
            body = b'<html><a href="/files/show.torrent">torrent</a></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        elif self.path == "/files/show.torrent":
            body = TORRENT_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "application/x-bittorrent")
        elif self.path == "/files/bad.torrent":
            body = b"not-a-torrent"
            self.send_response(200)
            self.send_header("Content-Type", "application/x-bittorrent")
        elif self.path == "/empty-page":
            body = b"<html><body>nothing here</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        else:
            body = b"not found"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    passed = 0
    total = 0

    def expect(name: str, actual, expected) -> None:
        nonlocal passed, total
        total += 1
        if actual == expected:
            passed += 1
        else:
            print(f"FAIL  {name}\n  expected: {expected!r}\n  actual:   {actual!r}")

    def raises(name: str, fn) -> None:
        nonlocal passed, total
        total += 1
        try:
            fn()
        except (RssError, Exception):
            passed += 1
        else:
            print(f"FAIL  {name}\n  expected exception")

    feed = parse_feed(MIKAN_RSS.encode("utf-8"), "https://mikanani.me/RSS/Test")
    expect("feed title", feed.title, "Mikan Project")
    expect("feed item count", len(feed.items), 3)
    first = feed.items[0]
    expect("mikan download kind", first.download_kind, "torrent")
    expect(
        "mikan download url",
        first.download_url,
        "https://mikanani.me/Download/2026/abc.torrent",
    )
    expect("mikan size", first.size, 123456789)
    expect("mikan date", first.published_at, "2026-10-26T03:00:00+00:00")
    second = feed.items[1]
    expect("mikan version", second.raw.get("download_kind"), "torrent")
    expect("mikan second guid", second.guid, "mikan-2")
    expect("remote key stable", first.remote_key, first.remote_key)
    expect(
        "remote key feed scoped",
        first.remote_key
        != parse_feed(
            MIKAN_RSS.encode("utf-8"), "https://mikanani.me/RSS/Other"
        ).items[0].remote_key,
        True,
    )

    config = normalize_config(
        {
            "feed_urls": ["https://mikanani.me/RSS/Test"],
            "keyword": "Dragon Ball",
            "max_per_run": 500,
        },
        "测试订阅",
    )
    expect("config default save dir", config["save_subdir"], "_订阅/测试订阅")
    expect("config max clamp", config["max_per_run"], 200)
    expect("config feed urls", config["feed_urls"], ["https://mikanani.me/RSS/Test"])

    unlimited = normalize_config(
        {
            "feed_urls": ["https://mikanani.me/RSS/Test"],
            "max_per_run": 0,
            "save_root": True,
        },
        "测试订阅",
    )
    expect("config unlimited", unlimited["max_per_run"], 0)
    expect("config root save dir", unlimited["save_subdir"], "")
    expect("config root flag", unlimited["save_root"], True)

    matched = [item for item in feed.items if is_candidate(item, config)]
    expect("keyword matches", len(matched), 3)
    config_regex = {
        **config,
        "keyword": "",
        "regex": r"S01E02 \[1080p\]\[HEVC",
    }
    matched = [item for item in feed.items if is_candidate(item, config_regex)]
    expect("regex matches one", [item.guid for item in matched], ["mikan-1"])
    config_size = {**config, "min_size_mb": 100.0}
    matched = [item for item in feed.items if is_candidate(item, config_size)]
    expect("size filter", [item.guid for item in matched], ["mikan-1", "mikan-2"])

    atom = parse_feed(ATOM_FEED.encode("utf-8"), "https://example.com/atom")
    expect("atom title", atom.title, "Atom Anime")
    expect("atom count", len(atom.items), 1)
    entry = atom.items[0]
    expect("atom download kind", entry.download_kind, "torrent")
    expect("atom download url", entry.download_url, "https://example.com/3.torrent")
    expect("atom link", entry.link, "https://example.com/post/3")
    expect("atom date", entry.published_at, "2026-10-25T12:00:00+00:00")

    magnet = parse_feed(MAGNET_RSS.encode("utf-8"), "https://example.com/rss")
    expect("magnet kind", magnet.items[0].download_kind, "magnet")
    expect(
        "magnet url",
        magnet.items[0].download_url,
        "magnet:?xt=urn:btih:abcdef0123456789&dn=Magnet.Show.S02E04",
    )

    rdf = parse_feed(RDF_FEED.encode("utf-8"), "https://example.com/rdf")
    expect("rdf count", len(rdf.items), 1)
    expect("rdf magnet", rdf.items[0].download_kind, "magnet")
    expect("rdf date", rdf.items[0].published_at, "2026-10-24T02:00:00+00:00")

    priority = parse_feed(PRIORITY_RSS.encode("utf-8"), "https://example.com/rss")
    expect("summary magnet priority kind", priority.items[0].download_kind, "magnet")
    expect(
        "summary magnet priority url",
        priority.items[0].download_url,
        "magnet:?xt=urn:btih:summary&dn=Priority.Show",
    )
    expect("torrent info hash", torrent_info_hash(TORRENT_BYTES), TORRENT_HASH)
    expect("torrent info hash garbage", torrent_info_hash(b"not-a-torrent"), "")

    expect(
        "feed url dedupe",
        normalize_feed_urls(
            "https://a.example/rss\nhttps://a.example/rss,https://b.example/rss"
        ),
        ["https://a.example/rss", "https://b.example/rss"],
    )
    raises("feed url invalid", lambda: normalize_feed_urls("ftp://example.com/rss"))
    raises(
        "feed url too many",
        lambda: normalize_feed_urls(
            [f"https://example.com/{index}" for index in range(21)]
        ),
    )
    raises(
        "config bad regex",
        lambda: normalize_config(
            {"feed_urls": "https://example.com/rss", "regex": "([bad"}, "X"
        ),
    )
    raises(
        "config traversal",
        lambda: normalize_config(
            {"feed_urls": "https://example.com/rss", "save_subdir": "../etc"}, "X"
        ),
    )
    raises("parse garbage", lambda: parse_feed(b"<not-xml", "https://example.com"))

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FeedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        fetched = fetch_feed(base + "/feed.xml")
        expect("fetch feed", len(fetched.items), 3)
        expect("fetch error", fetched.error, "")
        missing = fetch_feed(base + "/missing")
        expect("fetch 404", bool(missing.error), True)
        garbage = fetch_feed(base + "/garbage")
        expect("fetch bad xml", bool(garbage.error), True)
        magnet_url, magnet_kind = resolve_page_download(base + "/page")
        expect("page magnet kind", magnet_kind, "magnet")
        expect(
            "page magnet unescape",
            magnet_url,
            "magnet:?xt=urn:btih:page&dn=Page.Show",
        )
        torrent_url, torrent_kind = resolve_page_download(base + "/torrent-page")
        expect("page torrent kind", torrent_kind, "torrent")
        expect("page torrent absolute", torrent_url, base + "/files/show.torrent")
        torrent_magnet, torrent_magnet_kind = resolve_torrent_magnet(
            base + "/files/show.torrent"
        )
        expect("torrent magnet kind", torrent_magnet_kind, "magnet")
        expect(
            "torrent magnet hash",
            torrent_magnet,
            f"magnet:?xt=urn:btih:{TORRENT_HASH}",
        )
        page_magnet, page_kind = resolve_torrent_magnet(
            base + "/files/show.torrent", page_url=base + "/page"
        )
        expect("page magnet preferred kind", page_kind, "magnet")
        expect(
            "page magnet preferred url",
            page_magnet,
            "magnet:?xt=urn:btih:page&dn=Page.Show",
        )
        fallback_magnet, fallback_kind = resolve_torrent_magnet(
            base + "/files/show.torrent", page_url=base + "/torrent-page"
        )
        expect("torrent page fallback kind", fallback_kind, "magnet")
        expect(
            "torrent page fallback url",
            fallback_magnet,
            f"magnet:?xt=urn:btih:{TORRENT_HASH}",
        )
        raises(
            "bad torrent no magnet",
            lambda: resolve_torrent_magnet(base + "/files/bad.torrent"),
        )
        raises("page no download", lambda: resolve_page_download(base + "/empty-page"))
        raises("page 404", lambda: resolve_page_download(base + "/missing"))
    finally:
        server.shutdown()
        server.server_close()

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
