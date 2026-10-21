"""影视文件名解析器。

把 Scene / 字幕组 / 网盘 / 手动命名混在一起的文件名，解析成
标题、年份、季集、版本信息等结构化字段，供 TMDb 匹配使用。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from .tag_dictionary import custom_field_pattern, custom_fullmatch, is_custom_group

VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".flv",
    ".webm", ".rmvb", ".rm", ".mpg", ".mpeg", ".m4v", ".iso", ".strm", ".vob",
}
SIDECAR_EXTS = {
    ".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup", ".nfo", ".sbv",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100,
}

RESOLUTION_RE = re.compile(
    r"(?<![a-z0-9])(?:bd[ ._-]?1080p|hd[ ._-]?1080p|bd[ ._-]?720p|hd[ ._-]?720p|"
    r"8k|4k|2k|2160p|1440p|1080p|1080i|720p|576p|576i|480p|480i|"
    r"\d{3,4}[x*×]\d{3,4})(?![a-z0-9])",
    re.I,
)
# 顺序很重要：先长后短（避免 WEB-DL 被 WEB 抢走）；音频/编码先于来源，
# 避免来源规则把 DTS-HD / TrueHD 里的 HD 抢走
TAG_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "video",
        re.compile(
            r"(?i)(?<![a-z0-9])(?:x[ ._-]?264|x[ ._-]?265|h[ ._-]?264|h[ ._-]?265|hevc|avc|"
            r"av1|vp9|vp8|vp6|xvid|divx|mpeg[ ._-]?[124]|vc[ ._-]?1|realvideo|theora|"
            r"dirac|prores|avs)(?![a-z0-9])"
        ),
    ),
    (
        "audio",
        re.compile(
            r"(?i)(?<![a-z0-9])(?:dd[p+]?[ .]?[0-9][ .][0-9]|dd[p+]|dts[ ._-]?hd[ ._-]?ma|"
            r"dts[ ._-]?hd|dts[ ._-]?x|dts|true[ ._-]?hd|atmos|eac3|ac3|aac|lpcm|pcm|"
            r"hd[ ._-]?ma|flac|wav|alac|opus|mp3)(?![a-z0-9])"
        ),
    ),
    (
        "source",
        re.compile(
            r"(?i)(?<![a-z0-9])(?:web[ ._-]?dl|web[ ._-]?rip|web|amzn|nf|hs|blu[ ._-]?ray|bd[ ._-]?remux|"
            r"remux|bd[ ._-]?rip|br[ ._-]?rip|hd[ ._-]?tv[ ._-]?rip|hd[ ._-]?tv|hd[ ._-]?rip|"
            r"dvd[ ._-]?rip|dvd[ ._-]?scr|dvd|uhd[ ._-]?rip|hd[ ._-]?dvd|hd[ ._-]?cam|"
            r"vhs[ ._-]?rip|pdtv|dtv|"
            r"cam|camera|tc|ts|hc|r5|bd|hd|uhd)(?![a-z0-9])"
        ),
    ),
    (
        "hdr",
        re.compile(
            r"(?i)(?<![a-z0-9])(?:hdr10\+|hdr10|hdr[ ._-]?vivid|hdr|"
            r"dolby[ ._-]?vision|dobly[ ._-]?vison|dolby|do[ ._-]?vi|dv|sdr|edr|"
            r"hi10p|8bit|10bit|12bit)(?![a-z0-9])"
        ),
    ),
]
SOURCE_TAGS = {
    "webdl", "web", "webrip", "bluray", "blu", "bdrip", "brrip", "bdremux",
    "remux", "dvdrip", "dvd", "hdtv", "hdrip", "hdvd", "uhdrip", "hd", "ts",
    "tc", "hdcam", "cam", "vcd", "dvdscr", "bd", "uhd", "pdtv", "dtv",
    "vhsrip", "hdtvrip", "amzn", "nf", "hs",
}
VIDEO_TAGS = {
    "x264", "x265", "h264", "h265", "hevc", "avc", "av1", "vp9", "vp8", "vp6",
    "mpeg2", "mpeg1", "mpeg4", "vc1", "realvideo", "theora", "dirac", "prores",
    "10bit", "8bit", "hi10p", "xvid", "divx", "h263",
}
AUDIO_TAGS = {
    "ddp", "dd", "dd+", "dts", "dtshd", "dts-hd", "dtsx", "truehd", "atmos",
    "aac", "ac3", "eac3", "flac", "wav", "alac", "lpcm", "pcm", "mp3", "opus",
    "5.1", "7.1", "2.0", "1.0", "ma", "hd-ma", "5.1ch", "7.1ch",
}
LANG_TAGS = {
    "国语", "粤语", "国配", "台配", "日语", "韩语", "英语", "法语", "中字",
    "简中", "繁中", "繁体", "简体", "双语", "中日", "中英", "内嵌", "外挂",
    "字幕", "无字", "英语字幕", "chinese", "chs", "cht", "gb", "big5", "eng",
    "jpn", "jap", "kor", "cht", "zho", "chi",
}
MISC_TAGS = {
    "repack", "proper", "internal", "complete", "limited", "remastered",
    "uncut", "directors", "director", "cut", "imax", "openmatte", "3d",
    "hfr", "60fps", "120fps", "uhdbluray", "杜比", "全景声", "无水印",
    "未删减", "完整版", "高清", "超清", "标清", "抢鲜版", "抢版", "清晰",
    "片源", "压制", "收藏", "典藏",
}
# Filmix「附加信息表」发布组词库：点号分隔的组名（如 …AC3.EVO）没有「-」前缀，
# 识别末尾 token 时用该词库兜底
KNOWN_GROUPS = {
    "126811", "404", "73acg", "ade", "adweb", "agsvweb", "ailmweb", "aisr",
    "ajp69", "alt", "amiable", "ao", "applehot", "aric", "asap", "axxo", "ay",
    "bae", "batweb", "beast", "blacktv", "bmdru", "catedu", "chd", "chdtv",
    "chdweb", "chupapimuñañyo", "cmax", "cmct", "cmctv", "cmrg", "cnhk", "cnscg",
    "cruelty", "csweb", "ctrl", "ctrlhd", "d-z0n3", "dcop", "ddhdtv", "deflate",
    "dhtclub", "djweb", "don", "dreamhd", "dreamtv", "dtshdma", "eaiqc", "ebp",
    "esir", "etrg", "eureka", "eurekaƒ", "evo", "exren", "fattiger",
    "ffansaience", "fgt", "flame", "fltth", "flux", "framestor", "frds", "froge",
    "frogweb", "fxg", "ggez", "ghosts", "glhf", "gossip", "gotv", "gpthd",
    "harestv", "haresweb", "hbo", "hdarea", "hdchina", "hdctv", "hdh", "hdhive",
    "hdhome", "hdkweb", "hdmaniacs", "hdroad", "hds", "hdsstv", "hdsweb",
    "hdvweb", "hdwtv", "hhweb", "hidt", "hiqve", "hiveweb", "hotweb", "huawei",
    "huaye", "ilovehd", "ilovetv", "int-nowys", "iuhd", "jetix", "jkct", "jyk",
    "kitsune", "kook", "lanihd", "lazy", "leagueweb", "legi0n", "lelvetv", "lol",
    "mandarin-ops", "megusta", "minihd", "mixed", "mkvtv", "momoweb", "mteam",
    "mweb", "mysilu", "naisu", "nexusnf", "nogrp", "ntb", "ntg", "nyh", "ops",
    "ourtv", "pack", "pandaqt", "parkhd", "parktv", "pigonf", "pigoweb",
    "playweb", "poppi", "psa", "ptertv", "pterweb", "ptweb", "py", "qhstudio",
    "qoq", "quickio", "rarbg", "ree", "rlweb", "rovers", "sbaoai", "sc",
    "seeweb", "shaanig", "sic", "sinners", "smurf", "sonyhd", "spamkings",
    "sparks", "squalor", "srenix", "ssta", "tagweb", "taobaobt", "telesto",
    "tgweb", "tjupt", "tommy", "townley", "tptv", "tuoxie", "ubits", "ubtv",
    "ubweb", "vcb-studio", "wf", "wiki", "wlweb", "xiaomi", "xiebian", "yify",
    "yingweb", "ylptv", "zerotv", "zhuixinfan", "zmweb", "ztr", "至尊宝",
}
# 点号分隔的组名兜底时，纯「首字母大写」的英文词更可能是标题词，不作为组名
GROUP_TAIL_TITLE_RE = re.compile(r"[A-Z][a-z]+")
EDITION_RE = re.compile(
    r"(导演剪辑版|加长版|未删减版|未分级版|终极版|典藏版|修复版|重制版|特别版|完整版|"
    r"director'?s?[ .]?cut|theatrical[ .]?cut|extended(?:[ .]?(?:cut|edition|version))?|"
    r"unrated|unedited|restored[ .]?(?:cut|version)?|re-?release|international[ .]?cut|"
    r"special[ .]?edition|uncut(?:[ .]?version)?|final[ .]?cut|ultimate[ .]?edition|"
    r"redux|criterion|colorized|alternative[ .]?cut|rough[ .]?cut|work[ .]?print|"
    r"fan[ .]?edit|remastered(?:[ .]?version)?|(?<![a-z0-9])dc(?![a-z0-9])|imax)",
    re.I,
)
RELEASE_NOTES_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:real[ ._-]?proper|proper|repack|rerip|read[ ._-]?nfo|internal|"
    r"festival|dirfix|nfofix|samplefix|syncfix|fixed|subbed|dubbed|multi|retail|hybrid|"
    r"collectors|stv|proof|screener|promo|complete|limited)(?![a-z0-9])"
)
# 含这些字样的是语言 / 字幕说明，不应当作发布组
CJK_GROUP_REJECT = (
    "国语", "粤语", "中字", "字幕", "双语", "简中", "繁中", "简体", "繁体",
    "日语", "韩语", "英语", "特效", "内封", "内嵌", "外挂", "无水印", "压制",
)
EXTRA_RE = re.compile(
    r"(sample|trailer|teaser|preview|花絮|预告|特典|访谈|制作特辑|幕后|"
    r"片头|片尾|nCOP|nCED|menu|彩蛋)",
    re.I,
)
ANIME_HINT_RE = re.compile(
    r"(动漫|动画|番剧|新番|字幕组|简日|繁日|中日双语|GB\s*&?\s*BIG5|"
    r"Baha|LoliHouse|ANi|Nekomoe|kamigami|sakurato|喵萌|桜都|悠哈|诸神)",
    re.I,
)
SITE_RE = re.compile(
    r"[\[【(（][^\]】)）]*(?:www\.|\.com|\.net|\.org|\.cc|\.tv|\.me|"
    r"电影天堂|飘花|电影港|磁力|种子|字幕组|压制组|论坛|资源站)[^\]】)）]*[\]】)）]",
    re.I,
)
ONLY_NUMBER_RE = re.compile(r"^[\[【(（]?(\d{1,3})[\]】)）]?$")
MULTI_SPACE_RE = re.compile(r"\s{2,}")

EP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"(?<![a-z0-9])s(?:eason)?[ ._-]?(\d{1,2})[ ._-]*e(?:p)?[ ._-]?(\d{1,4})"
        r"(?:[ ._-]*(?:-|~|至|–|to)[ ._-]*e?(\d{1,4}))?(?![0-9])",
        re.I,
    ),
    re.compile(r"(?<![a-z0-9])(\d{1,2})x(\d{1,4})(?:[ ._-]*(?:-|~)(\d{1,4}))?(?![0-9])", re.I),
]
SEASON_RE = re.compile(r"(?:s(?:eason)?[ ._-]?(\d{1,2}))(?![0-9e])", re.I)
CN_SEASON_RE = re.compile(r"第[ ]?([一二三四五六七八九十百零两\d]{1,4})[ ]?季")
EP_ONLY_RE = re.compile(
    r"(?:^|[ ._\-\[【(（])(?:e|ep|episode|第)[ ._-]?(\d{1,4})"
    r"(?:[ ._-]*(?:-|~|至|–)[ ._-]*(?:e|ep)?[ ._-]?(\d{1,4}))?"
    r"(?:$|[ ._\-)\]】)）])",
    re.I,
)
CN_EP_RE = re.compile(r"第[ ]?([一二三四五六七八九十百零两\d]{1,4})[ ]?(?:集|话|話|期|回)")
BRACKET_EP_RE = re.compile(r"[\[【(（][ ]?(?:第)?[ ]?(\d{1,4})[ ]?(?:集|话|話)?[ ]?[\]】)）]")
YEAR_RE = re.compile(r"(?<![0-9])(19\d{2}|20\d{2}|21\d{2})(?![0-9])")
TMDB_ID_RE = re.compile(r"(?:tmdbid|tmdb)[ ._-]*[:\-]?[ ]?(\d{2,9})", re.I)
GROUP_CANDIDATE_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff._-]{1,24}$")


def chinese_to_int(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if not text:
        return None
    total, section, number = 0, 0, 0
    has_digit = False
    for ch in text:
        if ch not in CN_NUM:
            return None
        has_digit = True
        value = CN_NUM[ch]
        if value == 10:
            section += (number or 1) * 10
            number = 0
        elif value == 100:
            section = (section + (number or 1)) * 100
            number = 0
        else:
            number = value
    if not has_digit:
        return None
    return total + section + number


@dataclass
class ParsedName:
    raw: str = ""
    title: str = ""
    title_alt: str = ""
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    resolution: str = ""
    hdr: str = ""
    video: str = ""
    audio: str = ""
    source: str = ""
    edition: str = ""
    release_notes: str = ""
    group: str = ""
    tmdb_id: int | None = None
    media_type: str = ""
    is_extra: bool = False
    confidence: float = 0.0
    # 各类附加信息在原始文件名里的原文（保留大小写），供「自选信息拼接」使用
    tag_texts: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_video_file(name: str) -> bool:
    return "." in name and ("." + name.rsplit(".", 1)[1].lower()) in VIDEO_EXTS


def file_ext(name: str) -> str:
    return ("." + name.rsplit(".", 1)[1].lower()) if "." in name else ""


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def clean_stem(name: str) -> str:
    stem = normalize_unicode(name)
    stem = os_path_stem(stem)
    stem = SITE_RE.sub(" ", stem)
    return stem.strip()


def os_path_stem(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    dot = base.rfind(".")
    if dot > 0 and len(base) - dot <= 5 and base[dot + 1:].isalnum():
        return base[:dot]
    return base


def _strip_known_tokens(text: str) -> str:
    tokens = text.split()
    kept: list[str] = []
    for token in tokens:
        plain = token.strip("[]()（）【】,，.·-_").lower()
        if not plain:
            continue
        if (
            plain in SOURCE_TAGS
            or plain in VIDEO_TAGS
            or plain in AUDIO_TAGS
            or plain in LANG_TAGS
            or plain in MISC_TAGS
            or RESOLUTION_RE.fullmatch(plain)
        ):
            continue
        kept.append(token)
    return " ".join(kept)


def _looks_like_group(candidate: str) -> bool:
    if is_custom_group(candidate):
        return True
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", candidate):
        return False
    if re.fullmatch(r"\d+", candidate):
        return False
    # 单集标记（如 E04 / EP12）不是发布组
    if re.fullmatch(r"e(?:p)?\d{1,3}", candidate, re.I):
        return False
    if any(token in candidate for token in CJK_GROUP_REJECT):
        return False
    parts = [part for part in re.split(r"[._\-@]", candidate.lower()) if part]
    if not parts:
        return False
    ambiguous = {"dl", "hd", "bd", "web", "hdr", "dv", "uhd", "sdr", "ep", "e"}
    known = SOURCE_TAGS | VIDEO_TAGS | AUDIO_TAGS | MISC_TAGS
    normalized = {re.sub(r"[^a-z0-9+]", "", tag) for tag in known}
    normalized.discard("")
    for part in parts:
        if part in ambiguous:
            return False
        if any(pattern.fullmatch(part) for _, pattern in TAG_RULES):
            return False
        if RELEASE_NOTES_RE.fullmatch(part) or EDITION_RE.fullmatch(part):
            return False
        if custom_fullmatch("release_notes", part) or custom_fullmatch("edition", part):
            return False
        if RESOLUTION_RE.fullmatch(part):
            return False
        plain = re.sub(r"[^a-z0-9+]", "", part)
        if plain and plain in normalized:
            return False
    return len(candidate) <= 24


def _pick_match(matches: list[re.Match[str]]) -> re.Match[str] | None:
    """同一位置取最长匹配；不同位置取最先出现的匹配。"""
    if not matches:
        return None
    return min(matches, key=lambda match: (match.start(), -(match.end() - match.start())))


def _non_overlapping(matches: list[re.Match[str]]) -> list[re.Match[str]]:
    """按出现位置合并内置与自定义匹配，重叠时保留更长的那个。"""
    ordered = sorted(matches, key=lambda match: (match.start(), -(match.end() - match.start())))
    picked: list[re.Match[str]] = []
    for match in ordered:
        if any(match.start() < other.end() and match.end() > other.start() for other in picked):
            continue
        picked.append(match)
    return picked


def _tidy_title(title: str) -> tuple[str, str]:
    """Returns (primary_title, secondary_title)."""
    title = title.replace("_", " ").replace("\u3000", " ")
    # dots used as separators between latin/number tokens become spaces
    title = re.sub(r"(?<=[A-Za-z0-9\u4e00-\u9fff])\.(?=[A-Za-z0-9\u4e00-\u9fff])", " ", title)
    title = re.sub(r"[\[【(（][^\]】)）]*[\]】)）]", " ", title)
    # quality markers glued to CJK titles, e.g. "让子弹飞2010BD1280超清"
    title = re.sub(
        r"(?i)(?:hd|bd|dvd|web|hdtv|bluray|remux)?[ ._-]?\d{3,4}[pi]?(?=\s|$|超清|高清|蓝光|国语)",
        " ",
        title,
    )
    title = re.sub(r"(?i)\b(?:hd|bd|dvd|web|hdtv|bluray|remux|超清|高清|蓝光|国语|粤语|中字)\b", " ", title)
    title = re.sub(r"(?:超清|高清|蓝光|国语|粤语|中字|双语|简中|繁中|杜比视界|杜比全景声|杜比音效|杜比|全景声)", " ", title)
    title = _strip_known_tokens(title)
    title = re.sub(r"[-–—~|/]+", " ", title)
    title = re.sub(r"[·•]", " ", title)
    title = MULTI_SPACE_RE.sub(" ", title).strip(" .-_·,，")
    # split CJK and latin runs; prefer CJK as primary
    cjk = re.findall(r"[\u4e00-\u9fff][\u4e00-\u9fff0-9·：:、，。！？]*", title)
    latin = re.findall(r"[A-Za-z][A-Za-z0-9'&:\- ]*", title)
    cjk_text = " ".join(part.strip() for part in cjk).strip()
    latin_text = " ".join(part.strip() for part in latin).strip()
    # 单个字母通常是标签残留（x、h 等），丢掉
    latin_text = " ".join(
        token for token in latin_text.split() if len(token) > 1 or not token.isascii()
    ).strip()
    latin_text = re.sub(r"\s{2,}", " ", latin_text).strip()
    if cjk_text and len(cjk_text) >= 2:
        primary, secondary = cjk_text, latin_text
    elif latin_text:
        primary, secondary = latin_text, cjk_text
    else:
        primary, secondary = title.strip(), ""
    primary = re.sub(r"\s{2,}", " ", primary).strip()
    secondary = re.sub(r"\s{2,}", " ", secondary).strip()
    return primary, secondary


def parse_media_name(name: str, *, is_dir: bool = False) -> ParsedName:
    result = ParsedName(raw=name)
    stem = normalize_unicode(name)
    if not is_dir:
        stem = os_path_stem(stem)
    stem = SITE_RE.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    if not stem:
        return result

    working = stem
    parsed: dict[str, Any] = {}

    tmdb_match = TMDB_ID_RE.search(working)
    if tmdb_match:
        result.tmdb_id = int(tmdb_match.group(1))
        working = working[: tmdb_match.start()] + " " + working[tmdb_match.end():]

    # season / episode
    for pattern in EP_PATTERNS:
        match = pattern.search(working)
        if match:
            result.season = int(match.group(1))
            result.episode = int(match.group(2))
            if match.lastindex and match.lastindex >= 3 and match.group(3):
                result.episode_end = int(match.group(3))
            working = working[: match.start()] + " " + working[match.end():]
            break
    if result.season is None:
        season_match = CN_SEASON_RE.search(working)
        if season_match:
            result.season = chinese_to_int(season_match.group(1))
            working = working[: season_match.start()] + " " + working[season_match.end():]
    if result.season is None:
        season_match = SEASON_RE.search(working)
        if season_match:
            result.season = int(season_match.group(1))
            working = working[: season_match.start()] + " " + working[season_match.end():]

    if result.episode is None:
        cn_match = CN_EP_RE.search(working)
        if cn_match:
            result.episode = chinese_to_int(cn_match.group(1))
            working = working[: cn_match.start()] + " " + working[cn_match.end():]

    if result.episode is None:
        ep_match = EP_ONLY_RE.search(working)
        if ep_match:
            result.episode = int(ep_match.group(1))
            if ep_match.lastindex and ep_match.lastindex >= 2 and ep_match.group(2):
                result.episode_end = int(ep_match.group(2))
            working = working[: ep_match.start()] + " " + working[ep_match.end():]

    if result.episode is None:
        bracket_match = BRACKET_EP_RE.search(working)
        if bracket_match:
            result.episode = int(bracket_match.group(1))
            working = working[: bracket_match.start()] + " " + working[bracket_match.end():]

    only_number = ONLY_NUMBER_RE.match(working.strip())
    if result.episode is None and only_number and (is_dir or len(working.strip()) <= 6):
        result.episode = int(only_number.group(1))
        working = " " + working[only_number.end():]

    # year
    year_match = None
    for candidate in YEAR_RE.finditer(working):
        value = int(candidate.group(1))
        if 1900 <= value <= 2100:
            year_match = candidate
    if year_match:
        result.year = int(year_match.group(1))
        working = working[: year_match.start()] + " " + working[year_match.end():]
    if result.year:
        parsed["year"] = result.year

    # tags
    resolution = RESOLUTION_RE.search(working)
    if resolution:
        result.resolution = resolution.group(0).lower().replace("4k", "2160p").replace("8k", "4320p")
        result.tag_texts["resolution"] = resolution.group(0).strip()
        working = working[: resolution.start()] + " " + working[resolution.end():]

    # 发布组：取「-」/「@」之后的最后一个片段（如 XXX-GROUP、XXX-年糕），
    # 校验不是画质/来源/语言等标签
    stripped = working.strip()
    last_sep = max(stripped.rfind("-"), stripped.rfind("@"))
    if last_sep >= 0:
        candidate_group = stripped[last_sep + 1:].strip()
        if GROUP_CANDIDATE_RE.fullmatch(candidate_group) and (
            is_custom_group(candidate_group) or _looks_like_group(candidate_group)
        ):
            result.group = candidate_group
            result.tag_texts["group"] = candidate_group
            index = working.rfind(candidate_group)
            if index >= 0:
                working = working[:index] + " "

    # 标签：用规则在整串里匹配并移除，避免被分隔符拆散
    for field, pattern in TAG_RULES:
        matches = list(pattern.finditer(working))
        if not matches:
            continue
        raw_values = [re.sub(r"\s{2,}", " ", match.group(0).strip()) for match in matches]
        raw_values = [value for value in raw_values if value]
        values = [
            re.sub(r"-{2,}", "-", re.sub(r"[ _]+", "-", value.lower())).strip("-")
            for value in raw_values
        ]
        if field == "hdr":
            unique = [value for value in values if value]
            if unique and not result.hdr:
                result.hdr = ".".join(dict.fromkeys(unique))
        else:
            value = values[0]
            if field == "source" and not result.source:
                result.source = value
            elif field == "video" and not result.video:
                result.video = value
            elif field == "audio" and not result.audio:
                result.audio = value
        if raw_values and field not in result.tag_texts:
            result.tag_texts[field] = ".".join(dict.fromkeys(raw_values))
        for match in reversed(matches):
            working = working[: match.start()] + " " + working[match.end():]

    if not result.resolution:
        loose = re.search(r"(?<![0-9A-Za-z])(\d{3,4})[pi](?![0-9A-Za-z])", working, re.I)
        if loose:
            result.resolution = loose.group(0).lower()
            result.tag_texts.setdefault("resolution", loose.group(0))
            working = working[: loose.start()] + " " + working[loose.end():]

    edition_matches = list(EDITION_RE.finditer(working))
    edition_pattern = custom_field_pattern("edition")
    if edition_pattern:
        edition_matches.extend(edition_pattern.finditer(working))
    edition = _pick_match(edition_matches)
    if edition:
        edition_text = edition.group(0).strip()
        result.edition = edition_text
        result.tag_texts.setdefault("edition", edition_text)
        working = working[: edition.start()] + " " + working[edition.end():]

    release_matches = list(RELEASE_NOTES_RE.finditer(working))
    release_pattern = custom_field_pattern("release_notes")
    if release_pattern:
        release_matches.extend(release_pattern.finditer(working))
    release_notes = _non_overlapping(release_matches)
    if release_notes:
        raw_notes = [re.sub(r"\s{2,}", " ", match.group(0).strip()) for match in release_notes]
        raw_notes = [value for value in raw_notes if value]
        notes = [
            re.sub(r"-{2,}", "-", re.sub(r"[ _]+", "-", value.lower())).strip("-")
            for value in raw_notes
        ]
        if notes and not result.release_notes:
            result.release_notes = ".".join(dict.fromkeys(notes))
        if raw_notes:
            result.tag_texts.setdefault("release_notes", ".".join(dict.fromkeys(raw_notes)))
        for match in reversed(release_notes):
            working = working[: match.start()] + " " + working[match.end():]

    # 发布组兜底：Filmix 风格的组名常以点号分隔直接放在末尾（如 ….AC3.EVO），
    # 用发布组词库匹配最后一个 token；纯「首字母大写」英文词更可能是标题词，跳过
    if not result.group:
        tail_tokens = [token for token in re.split(r"[._\s\-@]+", working) if token]
        if len(tail_tokens) >= 2:
            candidate = tail_tokens[-1].strip("[]()（）【】,，·")
            key = candidate.lower()
            custom_group = is_custom_group(candidate)
            if custom_group or (
                key in KNOWN_GROUPS
                and not GROUP_TAIL_TITLE_RE.fullmatch(candidate)
                and key not in SOURCE_TAGS | VIDEO_TAGS | AUDIO_TAGS | MISC_TAGS
            ):
                result.group = candidate
                result.tag_texts["group"] = candidate
                index = working.rfind(candidate)
                if index >= 0:
                    working = working[:index] + " "

    if EXTRA_RE.search(stem):
        result.is_extra = True

    title_seed = working
    # remove leading numeric index like "01 " or "[01]"
    title_seed = re.sub(r"^[\[【(（]?\d{1,3}[\]】)）]?[ ._-]+", " ", title_seed)
    title, alt = _tidy_title(title_seed)

    if len(title) < 2 and not result.title:
        title, alt = _tidy_title(stem)
        result.confidence = 0.35
    else:
        result.confidence = 0.9

    result.title = title
    result.title_alt = alt

    if result.season is not None or result.episode is not None:
        result.media_type = "tv"
    if ANIME_HINT_RE.search(stem):
        result.media_type = result.media_type or "tv"
    if result.is_extra:
        result.confidence = min(result.confidence, 0.5)
    if not result.title or len(result.title) <= 1:
        result.confidence = min(result.confidence, 0.25)
    if result.episode is None and result.season is None and result.media_type != "tv":
        result.media_type = result.media_type or "movie"
    if result.year is None and result.media_type == "movie":
        result.confidence = min(result.confidence, 0.8)
    if result.episode is None and result.media_type == "tv":
        result.confidence = min(result.confidence, 0.6)

    return result


def normalize_title(text: str) -> str:
    text = normalize_unicode(text or "").lower()
    text = re.sub(r"[\s\-_.·:：,，。!！?？'\"()（）\[\]【】]+", "", text)
    for word in ("the", "a", "an"):
        if text.startswith(word) and len(text) > len(word) + 2:
            text = text[len(word):]
    return text


def title_similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    if a in b or b in a:
        ratio = max(ratio, 0.86)
    return ratio
