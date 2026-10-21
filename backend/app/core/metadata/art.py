from __future__ import annotations

import html
import random
from typing import Any

from ...db import get_setting
from .tmdb import TMDBClient, TMDBError


PALETTES = [
    ("#0f172a", "#155e75", "#67e8f9"),
    ("#1c1917", "#7f1d1d", "#fca5a5"),
    ("#111827", "#4c1d95", "#c4b5fd"),
    ("#082f49", "#0e7490", "#a5f3fc"),
    ("#1a2e05", "#4d7c0f", "#d9f99d"),
    ("#2e1065", "#6d28d9", "#ddd6fe"),
    ("#3f0d0d", "#b45309", "#fde68a"),
    ("#042f2e", "#0f766e", "#99f6e4"),
]


def poster_svg(title: str, subtitle: str = "", seed: str = "") -> str:
    rng = random.Random(seed or title)
    bg, mid, accent = rng.choice(PALETTES)
    title = html.escape(title or "Unknown")
    subtitle = html.escape(subtitle or "")
    lines = []
    if len(title) > 7:
        cut = max(4, len(title) // 2)
        lines = [title[:cut], title[cut:]]
    else:
        lines = [title]
    text_nodes = "".join(
        f'<text x="150" y="{330 + index * 46}" font-size="38" fill="#f8fafc" '
        f'text-anchor="middle" font-family="PingFang SC, Noto Sans SC, sans-serif">{line}</text>'
        for index, line in enumerate(lines[:3])
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
<defs>
  <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="{bg}"/>
    <stop offset="55%" stop-color="{mid}"/>
    <stop offset="100%" stop-color="{bg}"/>
  </linearGradient>
</defs>
<rect width="300" height="450" fill="url(#g)"/>
<circle cx="240" cy="80" r="120" fill="{accent}" opacity="0.18"/>
<circle cx="40" cy="380" r="90" fill="{accent}" opacity="0.12"/>
<rect x="20" y="20" width="260" height="410" rx="10" fill="none" stroke="{accent}" stroke-opacity="0.35"/>
{text_nodes}
<text x="150" y="{330 + min(len(lines), 3) * 46}" font-size="20" fill="{accent}" text-anchor="middle"
 font-family="PingFang SC, Noto Sans SC, sans-serif">{subtitle}</text>
</svg>"""


def backdrop_svg(title: str, seed: str = "") -> str:
    rng = random.Random((seed or title) + "-bd")
    bg, mid, accent = rng.choice(PALETTES)
    safe_title = html.escape(title or "")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="780" height="439" viewBox="0 0 780 439">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0%" stop-color="{bg}"/><stop offset="60%" stop-color="{mid}"/><stop offset="100%" stop-color="{bg}"/>
</linearGradient></defs>
<rect width="780" height="439" fill="url(#g)"/>
<circle cx="640" cy="90" r="180" fill="{accent}" opacity="0.16"/>
<text x="390" y="230" font-size="42" fill="#f8fafc" text-anchor="middle"
 font-family="PingFang SC, Noto Sans SC, sans-serif">{safe_title}</text>
</svg>"""
