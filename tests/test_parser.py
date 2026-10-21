from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.parser import parse_media_name  # noqa: E402


CASES = [
    # (filename, expectations)
    ("[电影天堂www.dy2018.com]让子弹飞2010BD1280超清.mkv",
     {"title": "让子弹飞", "year": 2010, "media_type": "movie"}),
    ("千与千寻.Spirited.Away.2001.BluRay.1080p.mkv",
     {"title": "千与千寻", "year": 2001, "resolution": "1080p"}),
    ("Oppenheimer.2023.2160p.WEB-DL.HDR.DDP5.1.Atmos.mkv",
     {"title": "Oppenheimer", "year": 2023, "resolution": "2160p", "hdr": "hdr"}),
    ("Lord.of.War.2005.2160p.UHD.BluRay.REMUX.HEVC.DV.HDR.DTS-HD.MA.TrueHD.7.1.Atmos-年糕.mkv",
     {"year": 2005, "hdr": "dv.hdr", "video": "hevc", "group": "年糕",
      "tag_texts": {"source": "UHD.BluRay.REMUX", "audio": "DTS-HD.MA.TrueHD.Atmos", "hdr": "DV.HDR"}}),
    ("三体.S01E01.2023.2160p.WEB-DL.x265.10bit.DDP5.1-REPACK.mkv",
     {"season": 1, "episode": 1, "hdr": "10bit", "video": "x265", "release_notes": "repack"}),
    ("Some.Movie.2020.1080p.Directors.Cut.BluRay.x264.mkv",
     {"edition": "Directors.Cut"}),
    ("Blade.Runner.1982.Final.Cut.1080p.BluRay.DTS.x264.mkv",
     {"edition": "Final.Cut", "tag_texts": {"edition": "Final.Cut"}}),
    ("老友记.1994.S01E01.mkv",
     {"title": "老友记", "season": 1, "episode": 1, "media_type": "tv"}),
    ("Friends.S01E03.1080p.WEB-DL.mkv",
     {"title": "Friends", "season": 1, "episode": 3}),
    ("庆余年.2019.第01集.4K.mkv",
     {"title": "庆余年", "season": None, "episode": 1}),
    ("三体.S02E05-E07.2160p.mkv",
     {"title": "三体", "season": 2, "episode": 5, "episode_end": 7}),
    ("鬼灭之刃.第01集.mkv", {"title": "鬼灭之刃", "episode": 1}),
    ("怪奇物语 S01E01 1080p.mkv", {"title": "怪奇物语", "season": 1, "episode": 1}),
    ("1x02 - 老友记.mkv", {"episode": 2}),
    ("4K.HDR.杜比视界.沙丘2.2024.mkv", {"year": 2024, "media_type": "movie"}),
    ("三体 (2023) tmdb:124364/S01/E01.mkv", {"episode": 1}),
    ("阿凡达2.sample.mkv", {"is_extra": True}),
    ("让子弹飞.预告片.Trailer.mp4", {"is_extra": True}),
    ("The.Matrix.1999.REMASTERED.1080p.BluRay.x264-GROUP.mkv",
     {"title": "The Matrix", "year": 1999, "group": "GROUP"}),
    # Filmix 典型样例：Dolby 视频质量 / DC 版本 / PROPER 发布标签 / 点号分隔的 EVO 组
    ("盗梦空间.2010.2160p.Dolby.BluRay.DC.PROPER.x265.AC3.EVO.mp4",
     {"title": "盗梦空间", "year": 2010, "resolution": "2160p", "hdr": "dolby",
      "source": "bluray", "edition": "DC", "release_notes": "proper",
      "video": "x265", "audio": "ac3", "group": "EVO",
      "tag_texts": {"source": "BluRay", "video": "x265", "audio": "AC3",
                    "hdr": "Dolby", "edition": "DC",
                    "release_notes": "PROPER", "group": "EVO"}}),
    ("老友记.1994.S02E03.2160p.Dolby.BluRay.DC.PROPER.x265.AC3.EVO.mp4",
     {"title": "老友记", "year": 1994, "season": 2, "episode": 3, "hdr": "dolby",
      "edition": "DC", "release_notes": "proper", "group": "EVO",
      "tag_texts": {"group": "EVO", "hdr": "Dolby", "release_notes": "PROPER"}}),
    # 末尾 token 撞上发布组词库的标题词不作为组名（Beast 是组名，但这里是标题词）
    ("美女与野兽.Beauty.and.the.Beast.2017.1080p.BluRay.x264.mkv",
     {"title": "美女与野兽", "year": 2017, "group": ""}),
    # AMZN 等平台来源应被识别，不残留进标题
    ("某剧.S01E01.1080p.AMZN.WEB-DL.DDP5.1.H.264.mkv",
     {"season": 1, "episode": 1, "source": "amzn"}),
]


def main() -> int:
    failures = 0
    for name, expected in CASES:
        parsed = parse_media_name(name)
        data = parsed.to_dict()
        for key, value in expected.items():
            actual = data.get(key)
            ok = actual == value
            if not ok and isinstance(value, dict) and isinstance(actual, dict):
                ok = all(actual.get(sub_key) == sub_value for sub_key, sub_value in value.items())
            if not ok and key == "title":
                ok = value and value in (data.get("title") or "") or (data.get("title") or "") in value
            if not ok:
                failures += 1
                print(f"FAIL {name}\n  {key}: expected={value!r} actual={actual!r}\n  parsed={data}")
    print(f"{len(CASES) - failures}/{len(CASES)} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
