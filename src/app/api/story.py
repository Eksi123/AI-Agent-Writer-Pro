"""检索与摘要重建（手册「六 → 路由树 · 检索 / 摘要」）。

检索源：大纲单文件 + `summaries/`，**限本项目**（`project_id` 过滤）。
FTS5 不可用时降级 `LIKE`，返回结构不变并置 `data.degraded=true`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.services import search as search_service

router = APIRouter(prefix="/novels/{novel_id}", tags=["story"])


@router.get("/search")
async def search(
    novel_id: str,
    session: AsyncSession = Depends(get_session),
    q: str = Query(min_length=1, max_length=200),
    scope: str = Query(default="all", pattern="^(all|outline|summary)$"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    """检索设置/大纲/摘要（★`project_id` 必须过滤，否则跨项目串数据）。"""
    return ok(await search_service.search_settings(session, novel_id, q, scope=scope, limit=limit))


@router.post("/summaries/rebuild")
async def rebuild_summaries(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """重建章节摘要与派生索引。

    M1 只做索引重建与统计；LLM 摘要生成（`summarize_chapter`）在 M4 接入。
    """
    result = await search_service.rebuild_summaries(session, novel_id)
    await session.commit()
    return ok(result)
