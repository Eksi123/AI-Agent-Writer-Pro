# -*- coding: utf-8 -*-
"""
模块: app.api.recycle
文件名: recycle.py
作者: xionggaoqing
时间: 2026-10-31
说明: 回收站 API（v1031）。

    路由：
        GET    /recycle                                    → 最近删除过章节的小说（最多 20 本）
        GET    /recycle/{novel_id}                         → 该小说的「卷 → 被删章节」目录
        POST   /recycle/{novel_id}/chapters/{cid}/restore  → 回收某章节（整本被删则连小说+大纲一起恢复）
        DELETE /recycle/{novel_id}/chapters/{cid}          → 彻底删除某章节（不可恢复）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.services import recycle

router = APIRouter(tags=["recycle"])


@router.get("/recycle")
async def list_recycle() -> dict:
    """回收站首页：最近删除过章节的小说（含整本被删的），只保留最近 20 本。"""
    return ok({"items": recycle.list_recycled_novels(), "max": recycle.MAX_NOVELS})


@router.get("/recycle/{novel_id}")
async def get_recycle(novel_id: str) -> dict:
    """某本小说在回收站里的目录：卷 → 该卷下被删除的章节。"""
    return ok(recycle.get_recycled_contents(novel_id))


@router.post("/recycle/{novel_id}/chapters/{chapter_id}/restore")
async def restore_chapter(
    novel_id: str, chapter_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """回收一个章节。

    整本小说被删除时，回收任一章节会**同时恢复小说项目与它的大纲**
    （返回 `novel_restored=true`，前端据此刷新左边栏项目列表）。
    """
    dto = await recycle.restore_chapter(session, novel_id, chapter_id)
    await session.commit()
    return ok(dto)


@router.delete("/recycle/{novel_id}/chapters/{chapter_id}")
async def purge_chapter(
    novel_id: str, chapter_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """彻底删除回收站里的一个章节（红色警示操作，不可恢复）。"""
    dto = await recycle.purge_chapter(session, novel_id, chapter_id)
    await session.commit()
    return ok(dto)
