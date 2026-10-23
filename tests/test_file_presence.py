"""「全部文件」真实性过滤的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_file_presence.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.file_presence import filter_existing, filter_existing_rows  # noqa: E402
from app.core.providers.base import Entry, FileSource, SourceError, SourceNotFound  # noqa: E402


class FakeSource(FileSource):
    type = "fake"

    def __init__(
        self,
        tree: dict[str, list[str]],
        broken: set[str] | None = None,
        missing: set[str] | None = None,
    ):
        super().__init__({})
        self.tree = tree
        self.broken = broken or set()
        self.missing = missing or set()
        self.calls: list[str] = []

    def list(self, path: str = "") -> list[Entry]:
        self.calls.append(path)
        if path in self.missing:
            raise SourceNotFound(f"目录不存在: {path}")
        if path in self.broken:
            raise SourceError(f"目录不可用: {path}")
        entries = []
        for name in self.tree.get(path, []):
            entries.append(
                Entry(
                    name=name,
                    path=f"{path}/{name}".strip("/"),
                    is_dir=False,
                    size=1,
                    mtime=0,
                )
            )
        return entries


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        print(f"PASS {name}")
        return True
    print(f"FAIL {name}: expected {expected!r}, got {actual!r}")
    return False


def main() -> int:
    source = FakeSource(
        {
            "": ["a.mkv"],
            "show": ["e01.mkv"],
        },
        broken={"broken"},
        missing={"gone"},
    )
    rows = [
        {"source_path": "a.mkv"},
        {"source_path": "b.mkv"},
        {"source_path": "show/e01.mkv"},
        {"source_path": "show/e02.mkv"},
        {"source_path": "broken/x.mkv"},
        {"source_path": "gone/y.mkv"},
    ]
    present, hidden, unverified = filter_existing(rows, source)

    passed = 0
    total = 0
    for name, actual, expected in [
        ("存在的条目", [row["source_path"] for row in present], ["a.mkv", "show/e01.mkv"]),
        (
            "已不存在数量（含目录已删除）",
            hidden,
            3,
        ),
        ("无法验证数量（临时错误）", unverified, 1),
        ("同一父目录只列一次", source.calls.count(""), 1),
        ("每个父目录各列一次", source.calls.count("show"), 1),
        ("不可访问目录只尝试一次", source.calls.count("broken"), 1),
        ("已删除目录只尝试一次", source.calls.count("gone"), 1),
    ]:
        total += 1
        if check(name, actual, expected):
            passed += 1

    # 详细结果：目录已删除归入「已不存在」，而不是「无法验证」。
    present2, hidden_rows, unverified_rows = filter_existing_rows(rows, source)
    for name, actual, expected in [
        ("详细存在条目", [row["source_path"] for row in present2], ["a.mkv", "show/e01.mkv"]),
        (
            "详细已不存在条目",
            sorted(row["source_path"] for row in hidden_rows),
            ["b.mkv", "gone/y.mkv", "show/e02.mkv"],
        ),
        ("详细无法验证条目", [row["source_path"] for row in unverified_rows], ["broken/x.mkv"]),
    ]:
        total += 1
        if check(name, actual, expected):
            passed += 1

    print(f"{passed}/{total} cases passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
