# -*- coding: utf-8 -*-
"""
模块: app.agent.tools.builtin.outline_recall
文件名: outline_recall.py
作者: xionggaoqing
时间: 2026-11-05
说明: Agent 内置工具 `outline_recall`：按需摘录大纲（渐进式披露）。

    按需获取原则：AI 不整篇 `read_file` 大纲，而是先精确摘录目标章小节；
    定位失败时回落 FTS 关键词检索，返回命中片段。摘录上限 1200 字，
    远小于整篇大纲（动辄数千至上万字），显著降低单次 run 的输入 token。

    入参三选一即可：
    - `chapter_title`（可带 `volume_title`）→ 精确定位该章「简介 + 衔接要点」；
    - `query` → 关键词检索大纲命中片段（人名 / 地名 / 情节词等）。
"""

from __future__ import annotations

import json

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from app.core.logging import get_logger
from app.db.session import session_scope

logger = get_logger(__name__)


@tool("outline_recall", parse_docstring=True)
async def outline_recall(
    runtime: ToolRuntime,
    volume_title: str = "",
    chapter_title: str = "",
    query: str = "",
    limit: int = 3,
) -> str:
    """按需摘录大纲内容（优先使用本工具，不要整篇读取大纲文件）。

    写作某章或修改大纲某章前，先用本工具取该章「简介 + 衔接要点」；
    需要跨章检索（人物、伏笔、设定）时传 query 做关键词检索。
    本工具只返回相关片段，比 read_file 整篇大纲节省大量上下文。

    Args:
        volume_title: 卷名（可选，如「第一卷 谪凡间」）。同名章节多卷时用于消歧。
        chapter_title: 章名（如「第五章 青衣谪凡凡间客」）。给出即做精确摘录。
        query: 关键词检索（chapter_title 未给出或精确定位失败时使用）。
        limit: 关键词检索返回条数上限（1-10，默认 3）。

    Returns:
        JSON 字符串：`{mode, found, brief}` 或 `{mode, found, hits}`；
        未命中时给出 `hint`，说明可尝试的检索方式。
    """
    context = getattr(runtime, "context", None)
    project_id = str(getattr(context, "project_id", "") or "")
    if not project_id:
        return json.dumps({"found": False, "error": "缺少项目上下文"}, ensure_ascii=False)

    chapter_title = (chapter_title or "").strip()
    volume_title = (volume_title or "").strip()
    query = (query or "").strip()
    limit = max(1, min(int(limit or 3), 10))

    if not chapter_title and not query:
        return json.dumps(
            {
                "found": False,
                "hint": "请提供 chapter_title（精确摘录）或 query（关键词检索）之一。",
            },
            ensure_ascii=False,
        )

    from app.services import outline_recall as recall_service

    try:
        async with session_scope() as db:
            if chapter_title:
                brief = await recall_service.chapter_brief(
                    db, project_id, volume_title=volume_title, chapter_title=chapter_title
                )
                if brief:
                    return json.dumps(
                        {"mode": "chapter", "found": True, "brief": brief},
                        ensure_ascii=False,
                    )
                # 精确定位失败 → 用章名当关键词回落检索
                fallback_query = query or chapter_title
                hits = await recall_service.search_outline(db, project_id, fallback_query, limit=limit)
                if hits:
                    return json.dumps(
                        {
                            "mode": "search",
                            "found": True,
                            "note": f"未精确匹配到章节「{chapter_title}」，以下为关键词检索命中片段。",
                            "hits": hits,
                        },
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {
                        "mode": "chapter",
                        "found": False,
                        "hint": (
                            f"大纲中未找到「{chapter_title}」相关小节。"
                            "请核对章名是否与大纲一致（可用 query 传人物或情节关键词再试）。"
                        ),
                    },
                    ensure_ascii=False,
                )

            hits = await recall_service.search_outline(db, project_id, query, limit=limit)
            if hits:
                return json.dumps(
                    {"mode": "search", "found": True, "query": query, "hits": hits},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "mode": "search",
                    "found": False,
                    "query": query,
                    "hint": "大纲中未检索到相关内容；可换关键词，或给出 chapter_title 精确摘录。",
                },
                ensure_ascii=False,
            )
    except Exception as exc:  # 兜底：任何异常都给明确错误，不静默空结果
        logger.error("outline_recall_failed", project_id=project_id, error=str(exc))
        return json.dumps({"found": False, "error": f"大纲摘录失败：{exc}"}, ensure_ascii=False)


__all__ = ["outline_recall"]
