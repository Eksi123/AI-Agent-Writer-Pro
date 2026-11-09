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
        tool_name = request.tool_call.get("name") or ""
        if tool_name not in ("write_file", "edit_file"):
            return await handler(request)

        args = request.tool_call.get("args") or {}
        virtual_path = str(args.get("file_path") or args.get("path") or "")
        change = classify_change(virtual_path) if virtual_path else {}
        chapter_id = change.get("chapter_id") if change else None
        project_id = str(change.get("project_id") or "") if change else ""
        lock_key = chapter_id or (f"outline:{project_id}" if change.get("is_outline") and project_id else None)

        context = getattr(request.runtime, "context", None)
        run_id = getattr(context, "run_id", "") or ""
        session_id = getattr(context, "session_id", "") or ""
        project_id = project_id or str(getattr(context, "project_id", "") or "")
        if not lock_key or not run_id:
            return await handler(request)

        from app.services.content_lock import content_locks

        # ★ 加锁必须在 handler **之前**。旧实现是 `result = await handler(request)`
        #   返回后才 acquire —— 而「首次落盘」恰恰是最脆弱的窗口：生成一章可达
        #   数十秒到数分钟，这期间用户 PUT /chapters/{id}/content 撞不到 40903，
        #   照常写入；随后 Agent 落盘把用户刚改的内容整章覆盖。双方都以为自己是
        #   最后写入者，且锁对这段窗口完全不起作用（等于没有锁）。
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
        else:
            # 同章已被**其它 run** 占用。这里不阻断写入（全局单飞下极罕见，而阻断
            # 有可能把合法流程彻底卡死），但必须留痕——它是并发覆盖的直接证据。
            logger.warning(
                "content_lock_busy_before_write",
                lock_key=lock_key,
                run_id=run_id,
                project_id=project_id,
            )

        # 锁不在本方法内释放：按既有设计由 run 收尾统一 `release_for_run`
        # （含异常与取消路径），另见 services/content_lock.py 的 TTL 兜底。
        return await handler(request)
