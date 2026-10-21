"""扫描目录跳过规则的回归测试。

运行：cd backend && .venv/bin/python ../tests/test_scan_skip.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.runner import _skip_dir  # noqa: E402


CASES = [
    ("Filmix.Trash", True),
    ("filmix.trash", True),
    ("FILMIX.TRASH", True),
    ("Some.Trash", True),
    (".trash", True),
    ("#recycle", True),
    ("@eaDir", True),
    (".hidden", True),
    ("Trash", False),
    ("The.Trash.Movie", False),
    ("Season 1", False),
    ("待整理", False),
]


def main() -> int:
    passed = 0
    for name, expected in CASES:
        actual = _skip_dir(name)
        if actual == expected:
            passed += 1
        else:
            print(f"FAIL {name!r}: expected {expected}, got {actual}")
    print(f"{passed}/{len(CASES)} cases passed")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
