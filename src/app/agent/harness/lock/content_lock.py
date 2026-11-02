# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.lock
文件名: content_lock.py
作者: xionggaoqing
时间: 2026-11-02
说明: `ContentLockMiddleware`（★v1101，自 loop/middleware/file_change.py 拆出）：
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
