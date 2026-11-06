# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.guard.read_scope
文件名: read_scope.py
作者: xionggaoqing
时间: 2026-11-05
说明: `ReadScopeGuardMiddleware`：大纲 / 章节正文的**读取授权守卫**。

    读取这两类内容 token 成本高，默认不主动注入上下文；AI 真要读时按四档放行：
    1. 会话已授权（read_grants 注册表）→ 放行；
    2. 本 run 是写作意图（用户消息命中「按大纲生成/修改正文」）→ 放行；
    3. 用户消息显式提及读取（读/查看/参考 + 章/卷/大纲/全书…）→ 放行并记会话授权；
    4. 否则弹确认卡（批准 / 本次会话允许 / 拒绝），复用审批中断机制。

    只拦 read_file / grep / glob 对 `/projects/{pid}/outline/**` 与
    `/projects/{pid}/chapters/**` 的访问；summaries/、attachments/ 等不拦。

    **边界**：本守卫挂在主 agent 的中间件栈上，**不覆盖子代理**——deepagents
    用 `create_sub_agent(spec)` 单独编译子代理，只带其 spec 自带的 middleware。
    这是刻意的设计：`recaller` 子代理的职责正是在隔离上下文里读摘要/正文再回传
    精炼结论（若也弹确认卡，用户要为同一次回顾点两次）。子代理读取的 token 成本
    天然有界——隔离上下文不进主对话，只有最终结论回传；具体范围由其 prompt 约束
    （摘要优先、正文按 offset/limit 节选）。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from app.agent.harness.approval.write_approval import (
    INTERRUPT_KIND,
    create_interrupt,
    find_decided,
    record_decision,
    set_run_status,
    _truncate_args,
)
from app.core.logging import get_logger
from app.services.read_grant import mentions_explicit_read

logger = get_logger(__name__)

_READ_TOOLS = ("read_file", "grep", "glob")

_OUTLINE_PREFIX = "/projects/{pid}/outline/"
_CHAPTERS_PREFIX = "/projects/{pid}/chapters/"


def _scope_of(virtual_path: str, project_id: str) -> str | None:
    p = (virtual_path or "").replace("\\", "/")
    if not project_id:
        return None
    if p.startswith(_OUTLINE_PREFIX.format(pid=project_id)):
        return "outline"
    if p.startswith(_CHAPTERS_PREFIX.format(pid=project_id)):
        return "chapters"
    return None


class ReadScopeGuardMiddleware(AgentMiddleware):
    """大纲 / 章节正文读取的授权守卫（四档判定，未授权弹确认卡）。"""

    name = "ReadScopeGuard"

    def __init__(self, allowed_decisions: tuple[str, ...] = ("approve", "approve_session", "reject")) -> None:
        super().__init__()
        self.allowed_decisions = list(allowed_decisions)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = request.tool_call.get("name") or ""
        if tool_name not in _READ_TOOLS:
            return await handler(request)

        args = dict(request.tool_call.get("args") or {})
        path = str(args.get("file_path") or args.get("path") or args.get("pattern") or "")
        context = getattr(request.runtime, "context", None)
        project_id = str(getattr(context, "project_id", "") or "")
        session_id = str(getattr(context, "session_id", "") or "")
        run_id = str(getattr(context, "run_id", "") or "")
        scope = _scope_of(path, project_id)
        if scope is None:
            return await handler(request)

        from app.agent.loop.runner import run_manager
        from app.services.read_grant import read_grants

        # ① 会话已授权
        if read_grants.is_granted(session_id, scope):
            return await handler(request)
        # ② 写作意图 run
        if run_manager.read_intent_of(run_id):
            return await handler(request)
        # ③ 用户消息显式提及读取 → 放行并记会话授权
        user_text = run_manager.user_text_of(run_id)
        if mentions_explicit_read(user_text):
            await read_grants.grant(session_id, scope)
            return await handler(request)

        # ④ 弹确认卡
        tool_call_id = request.tool_call.get("id") or ""
        reason = f"AI 想读取{'大纲' if scope == 'outline' else '章节正文'}内容：{path}"
        existing = await find_decided(run_id, tool_call_id)
        if existing is not None:
            return await self._apply(existing, request, handler, tool_name, scope, session_id, reason)

        payload = {
            "kind": INTERRUPT_KIND,
            "risk": "read_scope",
            "reason": reason,
            "tool_name": tool_name,
            "args": _truncate_args(args),
            "tool_call_id": tool_call_id,
            "decisions": self.allowed_decisions,
        }
        interrupt_id = await create_interrupt(context, payload)
        emit = getattr(context, "emit", None)
        if emit is not None:
            await emit.publish(
                "interrupt.request",
                {
                    "interrupt_id": interrupt_id,
                    "tool_name": tool_name,
                    "args": payload["args"],
                    "decisions": self.allowed_decisions,
                    "risk": "read_scope",
                    "reason": reason,
                },
            )
        await set_run_status(context, "awaiting_approval")

        decision = interrupt(payload)
        record = await record_decision(run_id, tool_call_id, interrupt_id, decision)
        return await self._apply(record, request, handler, tool_name, scope, session_id, reason)

    async def _apply(
        self,
        record: dict[str, Any],
        request: Any,
        handler: Any,
        tool_name: str,
        scope: str,
        session_id: str,
        reason: str,
    ) -> Any:
        decision = record.get("decision") or "approve"
        tool_call_id = request.tool_call.get("id") or ""
        if decision == "approve_session":
            from app.services.read_grant import read_grants

            await read_grants.grant(session_id, scope)
            return await handler(request)
        if decision == "approve":
            return await handler(request)
        return ToolMessage(
            content=(
                f"用户未允许读取该内容（{reason}）。请改用 outline_grep 工具获取大纲摘录，"
                "或直接在回答里询问用户是否需要读取相关章节。"
            ),
            tool_call_id=tool_call_id,
            name=tool_name,
        )
