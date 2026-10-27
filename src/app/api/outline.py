# -*- coding: utf-8 -*-
"""
模块: app.api.outline
文件名: outline.py
作者: xionggaoqing
时间: 2026-10-27
说明: 大纲读写与校验（手册「六 → 路由树 · 大纲」）。

    ★`PUT /novels/{id}/outline` 成功后执行 `sync_order_from_outline` 回填卷章 `sort_order`。
    检索与摘要重建见 `api/story.py`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.schemas import OutlineIn
from app.services import story

router = APIRouter(prefix="/novels/{novel_id}", tags=["outline"])


@router.get("/outline")
async def get_outline(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """读大纲单文件（`outline/{小说名称}-大纲.md`）。"""
    return ok(await story.read_outline(session, novel_id))


@router.put("/outline")
async def put_outline(
    novel_id: str, body: OutlineIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """写大纲；成功后回填卷章顺序（`sync_order_from_outline`）。"""
    dto = await story.write_outline(session, novel_id, body.content)
    await session.commit()
    return ok(dto)


@router.get("/outline/validate")
async def validate_outline(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """解析校验：归一化一致性、缺失清单、序数与顺序冲突、未登记项、错误行号。

    与 G0 复用同一份 `parse_outline`；校验结果不阻断保存，仅供前端警告条。
    """
    return ok(await story.validate_outline(session, novel_id))
