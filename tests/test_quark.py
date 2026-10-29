"""夸克客户端、链接解析与订阅过滤规则测试。

运行：cd backend && .venv/bin/python ../tests/test_quark.py
使用本地假接口和临时目录，不访问真实夸克账号。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["CINEMINT_DATA_DIR"] = tempfile.mkdtemp(prefix="cinemint-quark-")
os.environ["CINEMINT_DEMO"] = "false"

from quark_mock import MockQuarkServer  # noqa: E402

from app.core.cloud_paths import cloud_root_parts, map_cloud_folder  # noqa: E402
from app.core.parser import parse_media_name  # noqa: E402
from app.core.quark import (  # noqa: E402
    QuarkClient,
    QuarkShareCancelledError,
    QuarkShareError,
    is_share_cancelled_message,
    default_save_subdir,
    parse_share_link,
    safe_save_subdir,
    staging_paths,
)
from app.core.subscriptions import is_candidate, normalize_config  # noqa: E402


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

    # ---------- 链接与路径 ----------
    link = parse_share_link("https://pan.quark.cn/s/abc123def456?pwd=k9x2")
    expect("link id", link.share_id, "abc123def456")
    expect("link passcode", link.passcode, "k9x2")
    expect("raw id", parse_share_link("abc123def456").share_id, "abc123def456")
    try:
        parse_share_link("https://example.com/not-a-share")
        expect("invalid link raises", False, True)
    except QuarkShareError:
        expect("invalid link raises", True, True)

    expect(
        "include subdirs default off",
        normalize_config({"share_url": "https://pan.quark.cn/s/abc123def456"}, "订阅").get(
            "include_subdirs"
        ),
        False,
    )
    expect(
        "include subdirs on",
        normalize_config(
            {
                "share_url": "https://pan.quark.cn/s/abc123def456",
                "include_subdirs": True,
            },
            "订阅",
        ).get("include_subdirs"),
        True,
    )
    expect(
        "staging default",
        staging_paths("夸克网盘/待整理", "_订阅/测试")[0],
        "夸克网盘/待整理/_订阅/测试",
    )
    expect(
        "staging quark parts",
        staging_paths("夸克网盘/待整理", "_订阅/测试")[1],
        ["待整理", "_订阅", "测试"],
    )
    expect(
        "staging override",
        staging_paths("挂载/待整理", "_订阅/测试", cloud_root="我的夸克/待整理")[1],
        ["我的夸克", "待整理", "_订阅", "测试"],
    )
    # OpenList 挂载名改名：默认逻辑会去掉第一段挂载名，云盘侧相对路径不变。
    expect(
        "mount rename cloud root",
        cloud_root_parts("新挂载名/待整理"),
        ["待整理"],
    )
    expect(
        "mount rename map inside",
        map_cloud_folder("新挂载名/待整理", "", "待整理/For infuse"),
        "For infuse",
    )
    expect(
        "mount rename map root",
        map_cloud_folder("新挂载名/待整理", "", "待整理"),
        "",
    )
    expect(
        "mount rename map outside",
        map_cloud_folder("新挂载名/待整理", "", "其他/For infuse"),
        None,
    )
    expect(
        "mount rename staging openlist",
        staging_paths("新挂载名/待整理", "Picked")[0],
        "新挂载名/待整理/Picked",
    )
    expect(
        "mount rename staging cloud parts",
        staging_paths("新挂载名/待整理", "Picked")[1],
        ["待整理", "Picked"],
    )
    expect(
        "mount rename explicit cloud root",
        cloud_root_parts("新挂载名/待整理", "待整理"),
        ["待整理"],
    )
    expect(
        "mount rename explicit map",
        map_cloud_folder("新挂载名/待整理", "待整理", "待整理/For infuse"),
        "For infuse",
    )
    expect("default subdir", default_save_subdir("剧集/A"), "_订阅/剧集_A")
    expect("safe subdir strips traversal", safe_save_subdir("../etc/passwd", "X"), "etc/passwd")
    expect("safe subdir default", safe_save_subdir("", "X"), "_订阅/X")

    # ---------- 版本识别 ----------
    expect("version V2", parse_media_name("Show S01E01 [V2].mkv").version, 2)
    expect("version REPACK", parse_media_name("Show S01E01 REPACK.mkv").version, 2)
    expect("version REAL.PROPER", parse_media_name("Show S01E01 REAL.PROPER.mkv").version_label, "REAL.PROPER")
    expect("version none", parse_media_name("Show S01E01 1080p HEVC x265.mkv").version, 0)
    expect("version AV1 not V1", parse_media_name("Show S01E01 AV1.mkv").version, 0)

    # ---------- 过滤规则 ----------
    e01 = "[DKB] Dragon Ball Super Beerus - S01E01 [1080p][V2].mkv"
    e02 = "[DKB] Dragon Ball Super Beerus - S01E02 [1080p][HEVC x265 10bit].mkv"
    base = {"share_url": "https://pan.quark.cn/s/abc123def456"}
    config_none = normalize_config(base, "t")
    expect("filter none e01", is_candidate(e01, config_none), True)
    expect("filter none e02", is_candidate(e02, config_none), True)
    config_regex = normalize_config(
        {
            **base,
            "regex": r"^\[DKB\] Dragon Ball Super Beerus - S01E(\d+) "
            r"\[1080p\]\[HEVC x265 10bit\]\.mkv$",
        },
        "t",
    )
    expect("filter regex e01", is_candidate(e01, config_regex), False)
    expect("filter regex e02", is_candidate(e02, config_regex), True)
    config_keyword = normalize_config({**base, "keyword": "Dragon Ball Super Beerus"}, "t")
    expect("filter keyword e01", is_candidate(e01, config_keyword), True)
    expect("filter keyword e02", is_candidate(e02, config_keyword), True)
    config_both = normalize_config(
        {
            **base,
            "keyword": "Dragon Ball Super Beerus",
            "regex": r"S01E02",
        },
        "t",
    )
    expect("filter both e01", is_candidate(e01, config_both), False)
    expect("filter both e02", is_candidate(e02, config_both), True)
    config_ext = normalize_config({**base, "file_extensions": ".ass"}, "t")
    expect("filter ext", is_candidate(e01, config_ext), False)
    expect("filter ext other", is_candidate("note.ass", config_ext), True)

    # ---------- 假接口 ----------
    with MockQuarkServer() as server:
        share_id = "abc123def456"
        season = server.state.add_folder("Season 1", fid="season-1")
        server.state.add_file(e01, parent=season, fid="f1")
        server.state.add_file(e02, parent=season, fid="f2")
        server.state.add_file("notes.txt", parent=season, fid="f3")
        save_root = Path(tempfile.mkdtemp(prefix="cinemint-quark-save-"))
        server.state.save_root = save_root
        client = QuarkClient("ck", base_url=server.url, qps=0)
        try:
            expect("account test", client.test()["nickname"], "测试账号")
            # /member 不可用时（旧版接口 / 假接口）回退到 /account/info
            server.state.member_unavailable = True
            expect("account legacy fallback", client.test()["nickname"], "测试账号")
            server.state.member_unavailable = False
            server.state.require_passcode = "1"
            server.state.passcode = "abcd"
            try:
                client.share_token(share_id, "wrong")
                expect("wrong passcode", False, True)
            except QuarkShareError:
                expect("wrong passcode", True, True)
            server.state.share_token_error = ("分享不存在或已取消", 31001)
            try:
                client.share_token(share_id, "")
                expect("cancelled share raises", False, True)
            except QuarkShareCancelledError:
                expect("cancelled share raises", True, True)
            server.state.share_token_error = ("提取码错误", 31001)
            try:
                client.share_token(share_id, "wrong")
                expect("share permission error not cancelled", False, True)
            except QuarkShareCancelledError:
                expect("share permission error not cancelled", False, True)
            except QuarkShareError:
                expect("share permission error not cancelled", True, True)
            server.state.share_token_error = None
            expect("cancel hint true", is_share_cancelled_message("该分享已失效"), True)
            expect("cancel hint passcode false", is_share_cancelled_message("提取码错误"), False)
            server.state.require_passcode = ""
            stoken = client.share_token(share_id, "")
            expect("share token", stoken, "test-stoken")
            root_items = client.list_share(share_id, stoken)
            expect("share root folders", [item.name for item in root_items], ["Season 1"])
            files = client.walk_share(share_id, stoken)
            expect("walk files", sorted(item.name for item in files), sorted([e01, e02, "notes.txt"]))
            expect(
                "walk without subdirs skips nested files",
                client.walk_share(share_id, stoken, include_subdirs=False),
                [],
            )
            narrowed = client.walk_share(share_id, stoken, share_subdir="Season 1")
            expect("walk subdir", len(narrowed), 3)
            expect(
                "walk current dir without subdirs",
                len(
                    client.walk_share(
                        share_id,
                        stoken,
                        share_subdir="Season 1",
                        include_subdirs=False,
                    )
                ),
                3,
            )
            try:
                client.walk_share(share_id, stoken, share_subdir="不存在")
                expect("missing subdir", False, True)
            except QuarkShareError:
                expect("missing subdir", True, True)
            folder_fid = client.ensure_folder_path(["_订阅", "测试"])
            expect("ensure folder", bool(folder_fid), True)
            again = client.ensure_folder_path(["_订阅", "测试"])
            expect("ensure folder reuse", again, folder_fid)
            saved = client.save_share_files(
                share_id,
                stoken,
                fids=["f1"],
                tokens=["token-f1"],
                to_pdir_fid=folder_fid,
            )
            expect("save task", saved["task_id"], "task-1")
            task = client.wait_task(saved["task_id"])
            expect("task status", task["status"], 2)
            expect("save wrote file", (save_root / e01).read_bytes(), b"content:f1")
            listed = client.list_folder(folder_fid)
            expect("saved listed", [item.name for item in listed], [e01])
            saved_fid = listed[0].fid
            client.delete_files([saved_fid])
            expect("delete file", client.list_folder(folder_fid), [])
        finally:
            client.close()

    if passed != total:
        print(f"{passed}/{total} passed")
        return 1
    print(f"{passed}/{total} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
