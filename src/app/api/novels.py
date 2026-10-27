# -*- coding: utf-8 -*-
"""
模块: app.api.novels
文件名: novels.py
作者: xionggaoqing
时间: 2026-10-27
说明: 项目 CRUD、导出、阅读进度（手册「六 → 路由树 · 项目」）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.errors import DependencyUnavailableError, NotFoundError
from app.core.paths import exports_root
from app.core.response import ok
from app.db.base import utcnow
from app.db.models import Novel, ProjectIndex, ReadingProgress
from app.schemas import (
    NovelCreate,
    NovelDuplicate,
    NovelUpdate,
    ReadingProgressIn,
)
from app.services import audit, novels
from app.services.exporters import ExportTask, calibre_available, create_task, run_export

router = APIRouter(prefix="/novels", tags=["novels"])


@router.get("")
async def list_novels(session: AsyncSession = Depends(get_session)) -> dict:
    """项目列表（含 project_index 统计）。"""
    return ok(await novels.list_novels(session))


@router.post("")
async def create_novel(body: NovelCreate, session: AsyncSession = Depends(get_session)) -> dict:
    """创建项目：显式命名校验 + 初始化工作区与大纲模板。

    命名不合法返回 422 / `42201`。
    """
    dto = await novels.create_novel(
        session,
        title=body.title,
        author_name=body.author_name,
        genre=body.genre,
        synopsis=body.synopsis,
    )
    await session.commit()
    return ok(dto)


@router.get("/{novel_id}")
async def get_novel(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    return ok(await novels.get_novel_dto(session, novel_id))


@router.patch("/{novel_id}")
async def update_novel(
    novel_id: str, body: NovelUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    """重命名 / 改元信息（事务内同步重命名大纲文件）。"""
    dto = await novels.update_novel(
        session,
        novel_id,
        title=body.title,
        author_name=body.author_name,
        genre=body.genre,
        synopsis=body.synopsis,
    )
    await session.commit()
    return ok(dto)


@router.delete("/{novel_id}")
async def delete_novel(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """软删除：移入 `data/trash/` + `status=deleted`。"""
    result = await novels.delete_novel(session, novel_id)
    await session.commit()
    return ok(result)


@router.post("/{novel_id}/duplicate")
async def duplicate_novel(
    novel_id: str, body: NovelDuplicate, session: AsyncSession = Depends(get_session)
) -> dict:
    """复制项目（前端项目列表右键菜单「复制」）。"""
    dto = await novels.duplicate_novel(session, novel_id, new_title=body.title)
    await session.commit()
    return ok(dto)


# ---------------------------------------------------------------------------
# 阅读进度
# ---------------------------------------------------------------------------


@router.get("/{novel_id}/reading-progress")
async def get_reading_progress(
    novel_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    await novels.get_novel(session, novel_id)
    row = await session.get(ReadingProgress, novel_id)
    return ok(
        {
            "novel_id": novel_id,
            "chapter_id": row.chapter_id if row else None,
            "scroll_ratio": row.scroll_ratio if row else 0.0,
            "updated_at": row.updated_at if row else None,
        }
    )


@router.put("/{novel_id}/reading-progress")
async def put_reading_progress(
    novel_id: str,
    body: ReadingProgressIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """进度记忆：`novel_id` 主键单行。"""
    await novels.get_novel(session, novel_id)
    row = await session.get(ReadingProgress, novel_id)
    if row is None:
        row = ReadingProgress(novel_id=novel_id)
        session.add(row)
    row.chapter_id = body.chapter_id
    row.scroll_ratio = body.scroll_ratio
    row.updated_at = utcnow()
    await session.flush()
    return ok(
        {
            "novel_id": novel_id,
            "chapter_id": row.chapter_id,
            "scroll_ratio": row.scroll_ratio,
            "updated_at": row.updated_at,
        }
    )


# ---------------------------------------------------------------------------
# 导出（异步任务）
# ---------------------------------------------------------------------------

#: 进程内导出任务表（单机单用户，无需持久化；重启后任务丢失可重新导出）
_tasks: dict[str, ExportTask] = {}


@router.post("/{novel_id}/export")
async def export_novel(
    novel_id: str,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    format: str = Query(default="markdown", pattern="^(markdown|epub|mobi|txt)$"),
) -> dict:
    """异步导出 zip：正文按 format + `{小说名称}-大纲.md` 原件。

    calibre 未安装时 `mobi` **在受理阶段即**返回 503 / `50301`（前端据此置灰该选项），
    不推迟到后台任务失败。
    """
    dto = await novels.get_novel_dto(session, novel_id)

    if format == "mobi" and not calibre_available():
        raise DependencyUnavailableError(
            "未检测到 calibre，无法转换 mobi。请安装 calibre 后重试，或选择 epub/txt/markdown。"
        )

    task = create_task(_tasks, novel_id=novel_id, title=dto["title"], fmt=format)
    await audit.record(
        session,
        "novel.export",
        entity_type="novel",
        entity_id=novel_id,
        detail={"format": format, "task_id": task.task_id},
        commit=True,
    )
    background.add_task(run_export, _tasks, task.task_id, novel_id, format)
    return ok(task.to_dto())


@router.get("/{novel_id}/export-tasks/{task_id}")
async def get_export_task(novel_id: str, task_id: str) -> dict:
    task = _tasks.get(task_id)
    if task is None or task.novel_id != novel_id:
        raise NotFoundError(f"导出任务不存在：{task_id}")
    dto = task.to_dto()
    if task.file_path and task.file_path.exists():
        dto["size"] = task.file_path.stat().st_size
    return ok(dto)


@router.get("/{novel_id}/export-tasks/{task_id}/download")
async def download_export(novel_id: str, task_id: str) -> Any:
    from fastapi.responses import FileResponse

    task = _tasks.get(task_id)
    if task is None or task.novel_id != novel_id:
        raise NotFoundError(f"导出任务不存在：{task_id}")
    if task.status != "done" or not task.file_path or not task.file_path.exists():
        from app.core.errors import BadRequestError

        raise BadRequestError(f"导出产物尚未就绪（status={task.status}）")
    return FileResponse(
        task.file_path,
        media_type="application/zip",
        filename=task.file_path.name,
    )


@router.get("/{novel_id}/exports-dir")
async def exports_dir(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """导出产物目录（运维/排障）。"""
    await novels.get_novel(session, novel_id)
    return ok({"path": str(exports_root())})


# ---------------------------------------------------------------------------
# 索引重建
# ---------------------------------------------------------------------------


@router.post("/{novel_id}/index/rebuild")
async def rebuild_index(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """重建 `project_index`（清 dirty）。`story_fts` 全量重建在 M4 接入。"""
    await novels.get_novel(session, novel_id)
    idx: ProjectIndex = await novels.refresh_project_index(session, novel_id)
    await session.commit()
    return ok(
        {
            "project_id": novel_id,
            "chapter_count": idx.chapter_count,
            "total_words": idx.total_words,
            "last_updated": idx.last_updated,
            "dirty": idx.dirty,
        }
    )


@router.get("/{novel_id}/workspace-summary")
async def workspace_summary(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """工作区自检（文件 vs DB 一致性排障用）。"""
    novel: Novel = await novels.get_novel(session, novel_id)
    info = novels.workspace_summary(novel_id)
    info["db_title"] = novel.title
    info["db_outline_file"] = novel.outline_file
    row = await session.execute(select(ProjectIndex).where(ProjectIndex.project_id == novel_id))
    idx = row.scalar_one_or_none()
    info["index"] = (
        {
            "chapter_count": idx.chapter_count,
            "total_words": idx.total_words,
            "dirty": idx.dirty,
            "last_updated": idx.last_updated,
        }
        if idx
        else None
    )
    return ok(info)
