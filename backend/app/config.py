from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "CineFlow"
APP_NAME_ZH = "影序"
VERSION = "0.1.0"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.getenv("CINEFLOW_DATA_DIR", "./data")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "cineflow.db"
        self.secret_path = self.data_dir / "secret.key"
        self.demo_dir = self.data_dir / "demo-media"

        self.admin_password = os.getenv("CINEFLOW_ADMIN_PASSWORD", "cineflow")
        self.secret_key = os.getenv("CINEFLOW_SECRET_KEY", "")
        self.cookie_secure = _env_bool("CINEFLOW_COOKIE_SECURE", False)
        self.session_days = int(os.getenv("CINEFLOW_SESSION_DAYS", "14"))
        self.port = int(os.getenv("CINEFLOW_PORT", "8080"))
        self.demo_mode = _env_bool("CINEFLOW_DEMO", False)

        default_static = Path(__file__).resolve().parents[2] / "static"
        if not (default_static / "index.html").exists():
            # 本地开发未构建到 static/ 时，直接使用前端构建产物
            dist_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
            if (dist_dir / "index.html").exists():
                default_static = dist_dir
        self.static_dir = Path(os.getenv("CINEFLOW_STATIC_DIR", str(default_static)))
        self.cors_origins = [
            item.strip()
            for item in os.getenv(
                "CINEFLOW_CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if item.strip()
        ]

        # TMDb 在不同网络下可能需要换用备用入口；图片 CDN 也可单独覆盖。
        self.tmdb_api_base = (
            os.getenv("CINEFLOW_TMDB_API_BASE", "").strip().rstrip("/")
            or "https://api.themoviedb.org/3"
        )
        self.tmdb_image_base = (
            os.getenv("CINEFLOW_TMDB_IMAGE_BASE", "").strip().rstrip("/")
            or "https://image.tmdb.org/t/p"
        )


settings = Settings()
