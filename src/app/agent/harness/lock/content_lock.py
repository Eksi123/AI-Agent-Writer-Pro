# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.lock
文件名: content_lock.py
作者: xionggaoqing
时间: 2026-11-02
说明: `ContentLockMiddleware`：
    Agent 写章正文时给该章加「AI 编辑锁」，run 收尾统一释放。

    归属 harness：这是防并发写坏内容的**安全措施**，与文件变更通知
    （loop 的功能性中间件）职责不同，故拆开。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from app.agent.harness.sandbox.virtual_path import classify_change
from app.core.logging import get_logger

logger = get_logger(__name__)


class ContentLockMiddleware(AgentMiddleware):
    """Agent 写章正文时加「AI 编辑锁」（run 收尾统一释放）。

    锁键扩展到**大纲**——Agent 写 `outline/*.md` 时以 `outline:{project_id}`
    为键加锁，前端据此禁用大纲编辑与卷章/项目删除。
    """

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
        project_id = str(change.get("project_id") or "") if change else ""
        lock_key = chapter_id or (f"outline:{project_id}" if change.get("is_outline") and project_id else None)
        if not lock_key:
            return result

        context = getattr(request.runtime, "context", None)
        run_id = getattr(context, "run_id", "") or ""
        session_id = getattr(context, "session_id", "") or ""
        project_id = project_id or str(getattr(context, "project_id", "") or "")
        if not run_id:
            return result

        from app.services.content_lock import content_locks

        acquired = await content_locks.acquire(
            lock_key, run_id=run_id, session_id=session_id, project_id=project_id
        )
        if acquired:
            emit = getattr(context, "emit", None)
            if emit is not None:
                await emit.publish(
                    "content.lock",
                    {"chapter_id": lock_key, "run_id": run_id, "locked": True, "project_id": project_id},
                )
        return result
