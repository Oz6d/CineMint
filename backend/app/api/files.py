"""待整理目录的完整文件浏览 / 清理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db import query_one
from ..core.file_manager import delete_paths, list_directory, match_files, move_paths
from ..core.presence_cache import invalidate_library
from ..core.providers import build_source
from .deps import require_auth

router = APIRouter(
    prefix="/api/libraries",
    tags=["library-files"],
    dependencies=[Depends(require_auth)],
)


class MatchFilesBody(BaseModel):
    path: str = ""
    pattern: str = Field(min_length=1, max_length=200)
    mode: str = "contains"
    recursive: bool = False


class DeleteFilesBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=5000)


class MoveFilesBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=5000)
    destination: str = ""


def _library_source(library_id: int):
    library = query_one("SELECT * FROM libraries WHERE id=?", (library_id,))
    if not library:
        raise HTTPException(status_code=404, detail="影视库不存在")
    source_row = query_one("SELECT * FROM sources WHERE id=?", (library["source_id"],))
    if not source_row:
        raise HTTPException(status_code=400, detail="影视库的文件源不存在")
    return library, build_source(source_row)


def _ensure_idle(library_id: int) -> None:
    active = query_one(
        "SELECT id FROM tasks WHERE library_id=? AND status IN ('queued','running')",
        (library_id,),
    )
    if active:
        raise HTTPException(status_code=409, detail="该影视库已有任务在执行，请完成后再操作文件")


@router.get("/{library_id}/files")
def list_library_files(library_id: int, path: str = ""):
    library, source = _library_source(library_id)
    return list_directory(source, library["root_path"] or "", path)


@router.post("/{library_id}/files/match")
def match_library_files(library_id: int, body: MatchFilesBody):
    library, source = _library_source(library_id)
    if body.mode not in {"contains", "regex"}:
        raise HTTPException(status_code=400, detail="匹配方式不支持")
    return match_files(
        source,
        library["root_path"] or "",
        path=body.path,
        pattern=body.pattern,
        mode=body.mode,
        recursive=body.recursive,
    )


@router.post("/{library_id}/files/delete")
def delete_library_files(library_id: int, body: DeleteFilesBody):
    library, source = _library_source(library_id)
    _ensure_idle(library_id)
    result = delete_paths(source, library_id, library["root_path"] or "", body.paths)
    invalidate_library(library_id)
    if not result["deleted"] and result["failed"]:
        raise HTTPException(status_code=400, detail=result["failed"][0]["error"])
    return result


@router.post("/{library_id}/files/move")
def move_library_files(library_id: int, body: MoveFilesBody):
    library, source = _library_source(library_id)
    _ensure_idle(library_id)
    result = move_paths(
        source,
        library_id,
        library["root_path"] or "",
        body.paths,
        body.destination,
    )
    invalidate_library(library_id)
    return result
