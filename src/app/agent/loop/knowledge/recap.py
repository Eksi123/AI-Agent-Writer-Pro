# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.knowledge.recap
文件名: recap.py
作者: xionggaoqing
时间: 2026-10-27
说明: 前情提要构建（手册「二、(3) 知识库服务函数」）。

    - `build_recap`：前 N 章摘要（无摘要则正文节选）+ 跨卷衔接锚点，
      经 `RecapInjectionMiddleware.abefore_model` 注入系统提示，流程 S1 复用；
    - `build_pinned_chapters`：会话级「已读章节」上下文 Chips（手册「三、(3) 输入窗口」）。
"""

from __future__ import annotations

import re
from typing import Any

import frontmatter
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop.knowledge.chapters import list_chapters_ordered
from app.core.logging import get_logger
from app.core.paths import summary_file
from app.db.models import Novel
from app.services import files
from app.services.workspace import WorkspaceGuard

logger = get_logger(__name__)

#: 前情提要最多带的章数（近 N 章摘要 + 上一卷末章锚点）
_RECAP_CHAPTERS = 6
_RECAP_EXCERPT_LEN = 600


async def build_pinned_chapters(
    session: AsyncSession, project_id: str, chapter_ids: list[str]
) -> str:
    """构建「已读章节」上下文段（会话级 Chips，手册「三、(3) 输入窗口」）。

    每章给标题 + 摘要（无摘要则正文节选），供 RecapInjection 注入系统提示。
    """
    if not chapter_ids:
        return ""
    chapters = await list_chapters_ordered(project_id)
    by_id = {str(m.get("id")): m for m in chapters}
    lines: list[str] = ["## 用户指定的上下文章节"]
    for cid in chapter_ids[:8]:  # 上限 8 章，防提示词膨胀
        meta = by_id.get(cid)
        if meta is None:
            continue
        title = str(meta.get("title") or cid)
        vol_title = str(meta.get("volume_title") or "")
        head = f"### 【{vol_title} · {title}】" if vol_title else f"### 【{title}】"
        lines.append(head)
        summary = _read_summary_text(project_id, cid)
        if summary:
            lines.append(f"摘要：{summary[:500]}")
        else:
            try:
                _m, content = files.read_chapter(project_id, str(meta.get("volume_id")), cid)
                text = re.sub(r"\s+", " ", content or "").strip()
                lines.append(f"节选：{text[:500]}{'…' if len(text) > 500 else ''}")
            except (FileNotFoundError, OSError):
                continue
    return "\n".join(lines) if len(lines) > 1 else ""


async def build_recap(
    session: AsyncSession,
    project_id: str,
    *,
    before_chapter_id: str | None = None,
    max_chapters: int = _RECAP_CHAPTERS,
) -> str:
    """构建前情提要（前 N 章摘要，无摘要则取正文开头节选）。

    Args:
        before_chapter_id: 只汇总该章**之前**的章节（生成第 N 章时传第 N 章 id）。
    """
    chapters = await list_chapters_ordered(project_id)
    if not chapters:
        return ""

    if before_chapter_id:
        cut: list[dict[str, Any]] = []
        for meta in chapters:
            if meta.get("id") == before_chapter_id:
                break
            cut.append(meta)
        chapters = cut
    if not chapters:
        return "（这是全书第一章，暂无前情提要）"

    recent = chapters[-max_chapters:]
    guard = WorkspaceGuard(project_id)
    lines: list[str] = []

    novel = await session.get(Novel, project_id)
    if novel is not None:
        lines.append(f"书名：{novel.title}")
        if novel.genre:
            lines.append(f"题材：{novel.genre}")
        if novel.synopsis:
            lines.append(f"梗概：{novel.synopsis[:300]}")
        lines.append("")

    total = len(chapters)
    if total > len(recent):
        lines.append(f"（已写 {total} 章，以下仅列最近 {len(recent)} 章）")

    for meta in recent:
        chapter_id = str(meta.get("id"))
        title = str(meta.get("title") or chapter_id)
        vol_title = str(meta.get("volume_title") or "")
        head = f"- 【{vol_title} · {title}】" if vol_title else f"- 【{title}】"

        summary = _read_summary_text(project_id, chapter_id)
        if summary:
            lines.append(head + summary[:_RECAP_EXCERPT_LEN])
        else:
            excerpt = _read_chapter_excerpt(guard, project_id, meta)
            lines.append(head + (excerpt or "（暂无内容）"))

    # 上一卷末章锚点（跨卷衔接，流程 S1 复用）
    anchor = _cross_volume_anchor(project_id, chapters, recent)
    if anchor:
        lines.append("")
        lines.append(anchor)

    return "\n".join(lines).strip()


def _read_summary_text(project_id: str, chapter_id: str) -> str:
    path = summary_file(project_id, chapter_id)
    if not path.exists():
        return ""
    try:
        post = frontmatter.load(str(path))
        key_events = post.metadata.get("key_events")
        if isinstance(key_events, list) and key_events:
            return "；".join(str(e) for e in key_events).strip()
        return (post.content or "").strip()
    except Exception as exc:  # pragma: no cover
        logger.warning("summary_read_failed", chapter_id=chapter_id, error=str(exc))
        return ""


def _read_chapter_excerpt(guard: WorkspaceGuard, project_id: str, meta: dict[str, Any]) -> str:
    try:
        _m, content = files.read_chapter(project_id, str(meta.get("volume_id")), str(meta.get("id")))
    except (FileNotFoundError, OSError):
        return ""
    text = re.sub(r"\s+", " ", content or "").strip()
    if len(text) > _RECAP_EXCERPT_LEN:
        return text[:_RECAP_EXCERPT_LEN] + "…"
    return text


def _cross_volume_anchor(
    project_id: str, chapters: list[dict[str, Any]], recent: list[dict[str, Any]]
) -> str:
    """若最近章节不含上一卷内容，补一条上一卷末章锚点（跨卷衔接）。"""
    if not recent:
        return ""
    first_vol = recent[0].get("volume_id")
    earlier_other_vol = [c for c in chapters if c.get("volume_id") != first_vol]
    if not earlier_other_vol:
        return ""
    last = earlier_other_vol[-1]
    title = str(last.get("title") or "")
    vol_title = str(last.get("volume_title") or "")
    summary = _read_summary_text(project_id, str(last.get("id"))) or "（上一卷末章，详见正文）"
    return f"- ★上一卷衔接锚点【{vol_title} · {title}】{summary[:300]}"
