"""附加信息词库：可编辑词条与文件名识别回归测试。

运行：cd backend && .venv/bin/python ../tests/test_tag_dictionary.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.parser import parse_media_name  # noqa: E402
from app.core.tag_dictionary import (  # noqa: E402
    get_custom_tags,
    normalize_tag_dictionary,
    set_custom_tags,
)


def check(name: str, actual, expected) -> bool:
    if actual == expected:
        return True
    print(f"FAIL {name}: expected={expected!r} actual={actual!r}")
    return False


def main() -> int:
    failures = 0

    # 默认状态不能影响内置识别
    set_custom_tags({})
    parsed = parse_media_name(
        "盗梦空间.2010.2160p.Dolby.BluRay.DC.PROPER.x265.AC3.EVO.mp4"
    ).to_dict()
    failures += not check("默认 edition", parsed.get("edition"), "DC")
    failures += not check("默认 release_notes", parsed.get("release_notes"), "proper")
    failures += not check("默认 group", parsed.get("group"), "EVO")

    # 三个可编辑分类都能追加并被识别
    set_custom_tags(
        {
            "edition": ["十周年纪念版", "Diamond Edition"],
            "release_notes": ["V2", "REAL PROPER"],
            "group": ["MyGroup", "7³ACG"],
        }
    )
    failures += not check(
        "词库清理",
        get_custom_tags(),
        {
            "edition": ["十周年纪念版", "Diamond Edition"],
            "release_notes": ["V2", "REAL PROPER"],
            "group": ["MyGroup", "7³ACG"],
        },
    )

    parsed = parse_media_name(
        "某电影.2024.1080p.十周年纪念版.V2.x264.MyGroup.mkv"
    ).to_dict()
    failures += not check("自定义 edition", parsed.get("edition"), "十周年纪念版")
    failures += not check("自定义 release_notes", parsed.get("release_notes"), "v2")
    failures += not check("自定义 group", parsed.get("group"), "MyGroup")
    failures += not check("自定义 title", parsed.get("title"), "某电影")
    failures += not check(
        "自定义 tag_texts",
        parsed.get("tag_texts"),
        {
            "resolution": "1080p",
            "video": "x264",
            "edition": "十周年纪念版",
            "release_notes": "V2",
            "group": "MyGroup",
        },
    )

    # 带空格 / 点号的版本词条应允许分隔符互换
    parsed = parse_media_name(
        "Some.Movie.2024.Diamond.Edition.1080p.REAL.PROPER.x264-GROUP.mkv"
    ).to_dict()
    failures += not check("分隔符 edition", parsed.get("edition"), "Diamond.Edition")
    failures += not check("分隔符 release_notes", parsed.get("release_notes"), "real.proper")

    # 自定义发布组支持点号分隔的片尾 token
    parsed = parse_media_name("某电影.2024.1080p.x264.7³ACG.mkv").to_dict()
    failures += not check("点号自定义 group", parsed.get("group"), "73ACG")

    # 非法 / 重复 / 空白词条会被清理
    failures += not check(
        "词条规范化",
        normalize_tag_dictionary(
            {
                "edition": ["  十周年纪念版  ", "十周年纪念版", "", "   "],
                "release_notes": ["V2", "v2"],
                "group": ["My Group"],
            }
        ),
        {
            "edition": ["十周年纪念版"],
            "release_notes": ["V2"],
            "group": ["My Group"],
        },
    )

    # 清空后不能残留自定义识别
    set_custom_tags({})
    parsed = parse_media_name("某电影.2024.1080p.十周年纪念版.V2.x264.MyGroup.mkv").to_dict()
    failures += not check("清空 edition", parsed.get("edition"), "")
    failures += not check("清空 release_notes", parsed.get("release_notes"), "")
    failures += not check("清空 group", parsed.get("group"), "")

    print(f"{16 - failures}/16 cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
