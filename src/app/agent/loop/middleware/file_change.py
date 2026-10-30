# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.file_change
文件名: file_change.py
作者: xionggaoqing
时间: 2026-10-27
说明: `FileChangeNotifyMiddleware`：`awrap_tool_call`（工具成功后）发 SSE `file.changed`
    并触发派生索引同步（★v1029：大纲是参考资料，不再回填卷章 sort_order）。

    虚拟路径形如 `/projects/{pid}/chapters/v{vol}/c{ch}.md`，前端据此热更新正文/目录树。
"""

from __future__ import annotations

import re
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from app.core.logging import get_logger

logger = get_logger(__name__)

_WRITE_TOOLS = ("write_file", "edit_file", "delete")

_CHAPTER_PATH = re.compile(r"^/projects/[^/]+/chapters/v(?P<vol>[^/]+)/c(?P<ch>[^/]+)\.md$")
_OUTLINE_PATH = re.compile(r"^/projects/[^/]+/outline/.+\.md$")


def classify_change(virtual_path: str) -> dict[str, Any]:
    """把虚拟路径解析为变更信息（供事件与索引同步）。"""
    p = (virtual_path or "").replace("\\", "/")
    info: dict[str, Any] = {"virtual_path": p, "chapter_id": None, "is_outline": False, "in_project": False}
    if p.startswith("/projects/"):
        info["in_project"] = True
        parts = p.split("/")
        info["project_id"] = parts[2] if len(parts) > 2 else ""
        m = _CHAPTER_PATH.match(p)
        if m:
            info["chapter_id"] = m.group("ch")
            info["volume_id"] = m.group("vol")
        info["is_outline"] = bool(_OUTLINE_PATH.match(p))
    return info


class FileChangeNotifyMiddleware(AgentMiddleware):
    """文件变更通知 + 派生索引同步（大纲变更额外回填卷章顺序）。"""

    name = "FileChangeNotify"

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        result = await handler(request)
        tool_name = request.tool_call.get("name") or ""
        if tool_name not in _WRITE_TOOLS:
            return result

        status = getattr(result, "status", None)
        if status == "error":
            return result

        args = request.tool_call.get("args") or {}
        virtual_path = str(args.get("file_path") or args.get("path") or "")
        if not virtual_path:
            return result

        change = classify_change(virtual_path)
        if not change.get("in_project"):
            return result

        change_kind = "delete" if tool_name == "delete" else ("edit" if tool_name == "edit_file" else "write")
        context = getattr(request.runtime, "context", None)
        emit = getattr(context, "emit", None)
        project_id = getattr(context, "project_id", "") or change.get("project_id") or ""

        if emit is not None:
            await emit.publish(
                "file.changed",
                {
                    "virtual_path": change["virtual_path"],
                    "change_kind": change_kind,
                    "chapter_id": change.get("chapter_id"),
                    "is_outline": change.get("is_outline", False),
                },
            )

        await self._sync_derived(project_id, change)
        return result

    async def _sync_derived(self, project_id: str, change: dict[str, Any]) -> None:
        """同步派生索引（失败不阻断，标 dirty 待全量重建）。"""
        if not project_id:
            return
        try:
            from app.db.session import session_scope
            from app.services import novels as novels_service
            from app.services import search as search_service

            async with session_scope() as db:
                if change.get("is_outline"):
                    # ★v1029：大纲是参考资料，不再回填卷章 sort_order；只重建检索索引
                    await search_service.reindex_project(db, project_id)
                    await novels_service.refresh_project_index(db, project_id)
                elif change.get("chapter_id"):
                    await novels_service.refresh_project_index(db, project_id)
                    await search_service.reindex_project(db, project_id)
                else:
                    await novels_service.refresh_project_index(db, project_id)
        except Exception as exc:
            logger.warning("derived_index_sync_failed", project_id=project_id, error=str(exc))
            await self._mark_dirty(project_id)

    @staticmethod
    async def _mark_dirty(project_id: str) -> None:
        try:
            from sqlalchemy import update

            from app.db.models import ProjectIndex
            from app.db.session import session_scope

            async with session_scope() as db:
                await db.execute(
                    update(ProjectIndex).where(ProjectIndex.project_id == project_id).values(dirty=1)
                )
        except Exception:  # pragma: no cover
            pass
