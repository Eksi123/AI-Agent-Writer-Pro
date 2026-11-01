# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.file_change
文件名: file_change.py
作者: xionggaoqing
时间: 2026-10-27
说明: `FileChangeNotifyMiddleware`：`awrap_tool_call`（工具成功后）发 SSE `file.changed`
    并触发派生索引同步（★v1029：大纲是参考资料，不再回填卷章 sort_order）。

    虚拟路径形如 `/projects/{pid}/chapters/v{vol}/c{ch}.md`，前端据此热更新正文/目录树。

    ★v1101：`ContentLockMiddleware`——Agent 写入某章正文时给该章加「AI 编辑锁」，
    锁定期内用户保存（含自动保存）被 `PUT /chapters/{id}/content` 拒绝（409/40903）；
    锁在 run 收尾（`release_for_run`）统一释放，并广播 `content.lock` SSE 事件。
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


class ContentLockMiddleware(AgentMiddleware):
    """★v1101：Agent 写章正文时加「AI 编辑锁」（run 收尾统一释放）。"""

    name = "ContentLock"

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        result = await handler(request)
        tool_name = request.tool_call.get("name") or ""
        if tool_name not in ("write_file", "edit_file"):
            return result
        if getattr(result, "status", None) == "error":
            return result

        args = request.tool_call.get("args") or {}
        virtual_path = str(args.get("file_path") or args.get("path") or "")
        change = classify_change(virtual_path) if virtual_path else {}
        chapter_id = change.get("chapter_id") if change else None
        if not chapter_id:
            return result

        context = getattr(request.runtime, "context", None)
        run_id = getattr(context, "run_id", "") or ""
        session_id = getattr(context, "session_id", "") or ""
        project_id = getattr(context, "project_id", "") or change.get("project_id") or ""
        if not run_id:
            return result

        from app.services.content_lock import content_locks

        acquired = await content_locks.acquire(
            chapter_id, run_id=run_id, session_id=session_id, project_id=project_id
        )
        if acquired:
            emit = getattr(context, "emit", None)
            if emit is not None:
                await emit.publish(
                    "content.lock",
                    {"chapter_id": chapter_id, "run_id": run_id, "locked": True},
                )
        return result
