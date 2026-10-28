"""TMDb API / 图片地址覆盖测试。

运行：cd backend && .venv/bin/python ../tests/test_tmdb_base.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def _run(code: str, **env_overrides: str) -> str:
    env = os.environ.copy()
    env.pop("CINEMINT_TMDB_API_BASE", None)
    env.pop("CINEMINT_TMDB_IMAGE_BASE", None)
    with tempfile.TemporaryDirectory() as data_dir:
        env["CINEMINT_DATA_DIR"] = data_dir
        env.update(env_overrides)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()


def test_default_bases() -> None:
    payload = json.loads(
        _run(
            "import json; from app.core.metadata.tmdb import API_BASE, IMAGE_BASE; "
            "print(json.dumps({'api': API_BASE, 'image': IMAGE_BASE}))"
        )
    )
    assert payload == {
        "api": "https://api.themoviedb.org/3",
        "image": "https://image.tmdb.org/t/p",
    }


def test_override_bases_and_normalize_trailing_slash() -> None:
    payload = json.loads(
        _run(
            "import json; from app.core.metadata.tmdb import API_BASE, IMAGE_BASE; "
            "print(json.dumps({'api': API_BASE, 'image': IMAGE_BASE}))",
            CINEMINT_TMDB_API_BASE="https://api.tmdb.org/3/",
            CINEMINT_TMDB_IMAGE_BASE="https://cdn.example.test/t/p/",
        )
    )
    assert payload == {
        "api": "https://api.tmdb.org/3",
        "image": "https://cdn.example.test/t/p",
    }


def test_blank_override_falls_back_to_default() -> None:
    payload = json.loads(
        _run(
            "import json; from app.core.metadata.tmdb import API_BASE, IMAGE_BASE; "
            "print(json.dumps({'api': API_BASE, 'image': IMAGE_BASE}))",
            CINEMINT_TMDB_API_BASE="   ",
            CINEMINT_TMDB_IMAGE_BASE="",
        )
    )
    assert payload == {
        "api": "https://api.themoviedb.org/3",
        "image": "https://image.tmdb.org/t/p",
    }


def test_cast_thumb_uses_configured_image_base() -> None:
    output = _run(
        "from app.core.scraper import _cast_block; "
        "print(_cast_block({'cast': [{'name': 'A', 'role': 'B', 'profile_path': '/x.jpg'}]}))",
        CINEMINT_TMDB_IMAGE_BASE="https://cdn.example.test/t/p",
    )
    assert "https://cdn.example.test/t/p/w185/x.jpg" in output


if __name__ == "__main__":
    test_default_bases()
    test_override_bases_and_normalize_trailing_slash()
    test_blank_override_falls_back_to_default()
    test_cast_thumb_uses_configured_image_base()
    print("test_tmdb_base: 4/4 passed")
