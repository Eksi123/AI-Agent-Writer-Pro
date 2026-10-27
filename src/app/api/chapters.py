#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.api.chapters
文件名: chapters.py
作者: xionggaoqing
时间: 2026-10-27
说明: 正文读写（乐观锁）、修订快照与回滚（手册「六 → 路由树 · 卷章」）。

    ★`PUT content` 必带 `base_version`：不符返回 409 / `40901` 并回传最新内容。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.schemas import ContentIn, RewriteIn
from app.services import audit, volumes

router = APIRouter(prefix="/chapters", tags=["chapters"])


@router.get("/{chapter_id}/content")
async def get_content(chapter_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """读正文，返回 `version` 供前端保存时回传为 `base_version`。"""
    return ok(await volumes.read_content(session, chapter_id))


@router.put("/{chapter_id}/content")
async def put_content(
    chapter_id: str, body: ContentIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """写正文（乐观锁）。

    - `base_version` 与文件 `version` 不一致 → 409 / `40901`，`data` 内含最新内容；
    - 成功则 `version += 1`，手动保存产生 `chapter_revisions` 快照，`autosave` 不快照。
    """
    dto = await volumes.write_content(
        session,
        chapter_id,
        content=body.content,
        base_version=body.base_version,
        source="manual",
        is_autosave=body.autosave,
    )
    await session.commit()
    return ok(dto)


@router.post("/{chapter_id}/rewrite")
async def rewrite_selection(
    chapter_id: str, body: RewriteIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """选区 AI 改写（改写/扩写/缩写/润色）：服务层直连 LLM，**不写入文件**。

    返回原文与改写结果，由前端做 diff 预览；用户「接受」后再走
    `PUT /chapters/{cid}/content`（乐观锁 + 修订快照）落盘，「放弃」则不产生任何改动。
    """
    from app.agent.llm.rewrite import rewrite_selection as do_rewrite

    # 校验章节存在并取章标题（改写风格参考）
    content_dto = await volumes.read_content(session, chapter_id)

    dto = await do_rewrite(
        session,
        selected_text=body.selected_text,
        mode=body.mode,
        model_ref=body.model_ref,
        chapter_title=str(content_dto.get("title") or ""),
        context_before=body.context_before or "",
        context_after=body.context_after or "",
        instruction=body.instruction or "",
    )
    await audit.record(
        session,
        "chapter.rewrite",
        entity_type="chapter",
        entity_id=chapter_id,
        detail={
            "mode": body.mode,
            "model_ref": dto["model_ref"],
            "original_chars": dto["word_counts"]["original"],
            "rewritten_chars": dto["word_counts"]["rewritten"],
        },
    )
    await session.commit()
    return ok(dto)


@router.get("/{chapter_id}/revisions")
async def list_revisions(chapter_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """修订快照列表（不含正文全文，只给 preview）。"""
    return ok(await volumes.list_revisions(session, chapter_id))


@router.post("/{chapter_id}/revisions/{revision_id}/restore")
async def restore_revision(
    chapter_id: str, revision_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """回滚到指定快照：先快照当前内容，再覆盖，`version` 继续自增。"""
    dto = await volumes.restore_revision(session, chapter_id, revision_id)
    await session.commit()
    return ok(dto)
