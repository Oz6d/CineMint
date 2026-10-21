from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import auth, files, images, items, libraries, overview, sources, tasks
from .api import settings as settings_api
from .api.demo import seed_demo
from .config import VERSION, settings
from .core.providers import SourceError
from .core.runner import runner
from .core.tag_dictionary import load_custom_tags_from_db
from .db import execute, get_setting, init_db, now, query_one, set_setting
from .security import hash_password


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_custom_tags_from_db()
    if not get_setting("admin_password_hash", ""):
        set_setting("admin_password_hash", hash_password(settings.admin_password))
    if get_setting("password_changed") is None:
        set_setting("password_changed", False)
    # 服务重启后，把中断的任务标记为失败，避免任务中心出现“永远在跑”的假象
    execute(
        "UPDATE tasks SET status='failed', error='服务重启导致任务中断', finished_at=? "
        "WHERE status IN ('queued','running')",
        (now(),),
    )
    if settings.demo_mode and not query_one("SELECT id FROM libraries LIMIT 1"):
        seed_demo()
    runner.start()
    yield
    runner.stop()


app = FastAPI(title="影序 CineFlow", version=VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SourceError)
async def source_error_handler(_: Request, exc: SourceError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(auth.router)
app.include_router(images.router)
app.include_router(overview.router)
app.include_router(sources.router)
app.include_router(libraries.router)
app.include_router(files.router)
app.include_router(items.router)
app.include_router(tasks.router)
app.include_router(settings_api.router)


@app.get("/api")
def api_root():
    return {"app": "影序 CineFlow", "version": VERSION, "docs": "/docs"}


static_dir = settings.static_dir
if (static_dir / "index.html").exists():
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = (static_dir / full_path).resolve()
        if full_path and static_dir in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")
else:

    @app.get("/", include_in_schema=False)
    def dev_root():
        return {
            "app": "影序 CineFlow",
            "version": VERSION,
            "message": "前端静态文件未构建。开发模式请运行 Vite（npm run dev），或执行 npm run build。",
        }
