# -*- coding: utf-8 -*-
"""
模块: app.api.story
文件名: story.py
作者: xionggaoqing
时间: 2026-10-27
说明: 检索与摘要重建（手册「六 → 路由树 · 检索 / 摘要」）+ ★v1102 固定流程入口
    （全书一致性审查 / 导出成书预检，实现均在 `agent/workflows/`）。

    检索源：大纲单文件 + `summaries/`，**限本项目**（`project_id` 过滤）。
    FTS5 不可用时降级 `LIKE`，返回结构不变并置 `data.degraded=true`。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.services import audit as audit_service
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

    摘要由 LLM 为已定稿章节生成（未配置模型时自动跳过）；随后全量重建派生索引与统计。
    """
    result = await search_service.rebuild_summaries(session, novel_id)
    await session.commit()
    return ok(result)


# ---------------------------------------------------------------------------
# ★v1102：固定流程（agent/workflows/）
# ---------------------------------------------------------------------------


@router.post("/audit")
async def run_consistency_audit(novel_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """全书一致性审查（workflows/book_consistency，A1→A3）。

    按卷分批直连 LLM 审查（时间线/人物/设定/伏笔/简介匹配），
    报告落 `outline/consistency-audit.md`，结构化结果回传前端。
    未配置模型时返回 `status=skipped`（不报错）。
    """
    from app.agent.workflows.book_consistency.audit import run_book_consistency_audit

    result = await run_book_consistency_audit(session, novel_id)
    await audit_service.record(
        session,
        "workflow.book_audit",
        entity_type="novel",
        entity_id=novel_id,
        detail={"status": result.get("status"), "issues": len(result.get("issues") or [])},
        commit=True,
    )
    return ok(result)


@router.get("/export-preflight")
async def export_preflight(
    novel_id: str,
    session: AsyncSession = Depends(get_session),
    format: str = Query(default="markdown", pattern="^(markdown|epub|mobi|txt)$"),
) -> dict:
    """导出成书预检（workflows/export_package，E1→E4）。

    纯规则 checklist（结构完整性/内容完备性/格式可用性），`passed=false` 仅表示存在
    阻断项（如无章节、mobi 缺 calibre）；警告项由用户自行决定是否继续导出。
    """
    from app.agent.workflows.novel_export.preflight import run_export_preflight

    return ok(await run_export_preflight(session, novel_id, format))
