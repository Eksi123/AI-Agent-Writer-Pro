# -*- coding: utf-8 -*-
"""
模块: app.services.outline_recall
文件名: outline_recall.py
作者: xionggaoqing
时间: 2026-11-05
说明: 大纲按需摘录（按关键词定位目标章，代替整篇读取大纲）。

    写作/改纲时不再整篇读大纲，而是精确摘录目标章小节：
    - `chapter_brief`：`parse_outline().find_chapter` 按归一化「卷名+章名」定位，
      返回「卷名 + 章名 + 简介 + 衔接要点」（≤1200 字）；
    - `search_outline`：定位失败时回落 FTS（kind=outline）做关键词检索，
      返回命中段落预览。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services import search as search_service
from app.services import story
from app.services.outline import parse_outline

logger = get_logger(__name__)

_BRIEF_LIMIT = 1200


async def chapter_brief(
    session: AsyncSession,
    project_id: str,
    *,
    volume_title: str = "",
    chapter_title: str = "",
) -> str:
    """精确摘录某章的大纲小节；找不到返回空串。"""
    if not chapter_title:
        return ""
    try:
        data = await story.read_outline(session, project_id)
    except Exception as exc:
        logger.warning("outline_recall_read_failed", project_id=project_id, error=str(exc))
        return ""
    parsed = parse_outline(data.get("content") or "")
    section = parsed.find_chapter(volume_title, chapter_title)
    if section is None and volume_title:
        # 卷名不匹配时退而按章名全局找
        for vol in parsed.volumes:
            hit = next((c for c in vol.chapters if c.normalized == _norm(chapter_title)), None)
            if hit is not None:
                section = hit
                volume_title = vol.title
                break
    if section is None:
        return ""
    lines = [f"【{volume_title} · {chapter_title}】"]
    if section.intro.strip():
        lines.append(f"简介：{section.intro.strip()}")
    if section.link_note.strip():
        lines.append(f"衔接要点：{section.link_note.strip()}")
    text = "\n".join(lines)
    return text[:_BRIEF_LIMIT]


async def search_outline(
    session: AsyncSession, project_id: str, query: str, limit: int = 3
) -> list[dict[str, Any]]:
    """关键词检索大纲（FTS 优先、LIKE 降级），返回命中条目列表。"""
    if not (query or "").strip():
        return []
    try:
        result = await search_service.search_settings(
            session, project_id, query=query, scope="outline", limit=limit
        )
    except Exception as exc:
        logger.warning("outline_recall_search_failed", project_id=project_id, error=str(exc))
        return []
    return result.get("items") or []


def _norm(title: str) -> str:
    from app.services.outline import normalize_title

    return normalize_title(title)
