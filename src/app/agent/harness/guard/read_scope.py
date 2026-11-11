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

    ★ 批量审批：同一 run 内短时间（200ms）的多个 read_file 请求合并为一张审批卡片，
      用户一次批准即可读取所有文件，避免逐个点击。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from app.agent.harness.approval.write_approval import (
    INTERRUPT_KIND,
    _truncate_args,
    create_interrupt,
    find_decided,
    humanize_virtual_path,
    record_decision,
    set_run_status,
)
from app.agent.harness.sandbox.virtual_path import normalize_virtual_path
from app.core.logging import get_logger
from app.services.read_grant import mentions_explicit_read

logger = get_logger(__name__)

_READ_TOOLS = ("read_file", "grep", "glob")

_OUTLINE_PREFIX = "/projects/{pid}/outline/"
_CHAPTERS_PREFIX = "/projects/{pid}/chapters/"

#: 批量审批收集窗口（秒）：同一 run 内 200ms 的多个 read 请求合并为一张卡片
_BATCH_WINDOW_SEC = 0.2


def _scope_of(virtual_path: str, project_id: str) -> str | None:
    """判定路径属于哪个受控读取域（None = 不受控，放行）。

    入参须为**已归一化**的虚拟路径（见 `normalize_virtual_path`）。
    """
    p = virtual_path or ""
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
        #: run 级待审批队列：{run_id: [(tool_call_id, path, args, timestamp), ...]}
        self._pending_reads: dict[str, list[tuple[str, str, dict, float]]] = {}
        #: run 级批量审批锁（防止并发创建多个 interrupt）
        self._batch_locks: dict[str, asyncio.Lock] = {}

    async def _get_batch_lock(self, run_id: str) -> asyncio.Lock:
        """获取 run 级批量审批锁（懒初始化）。"""
        if run_id not in self._batch_locks:
            self._batch_locks[run_id] = asyncio.Lock()
        return self._batch_locks[run_id]

    async def _collect_pending_reads(self, run_id: str, tool_call_id: str, path: str, args: dict) -> list[tuple[str, str, dict]]:
        """收集短时间内的多个 read 请求，返回 (tool_call_id, path, args) 列表。"""
        now = time.monotonic()
        pending = self._pending_reads.setdefault(run_id, [])
        pending.append((tool_call_id, path, args, now))

        # 等待收集窗口，看是否有更多请求
        await asyncio.sleep(_BATCH_WINDOW_SEC)

        # 清理过期请求（超过 1 秒的视为独立请求）
        cutoff = now - 1.0
        self._pending_reads[run_id] = [(tc, p, a, t) for tc, p, a, t in pending if t > cutoff]

        # 返回当前批次（所有在窗口内的请求）
        result = [(tc, p, a) for tc, p, a, t in self._pending_reads[run_id] if t >= now - _BATCH_WINDOW_SEC]
        return result

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_name = request.tool_call.get("name") or ""
        if tool_name not in _READ_TOOLS:
            return await handler(request)

        args = dict(request.tool_call.get("args") or {})
        # ★ 先归一化再判 scope：框架 `validate_path` 会把 `projects/{pid}/chapters/...`
        #   （相对路径）、`\projects\...`（反斜杠）补成 `/projects/{pid}/chapters/...`
        #   后照常读取。按字面入参判定会让整章正文**无确认卡**进入上下文，
        #   而本守卫存在的理由正是「正文 token 成本高、默认不主动读」。
        #   grep/glob 的 `pattern` 是搜索正则而非路径，不再当路径兜底（语义混乱）。
        path = normalize_virtual_path(str(args.get("file_path") or args.get("path") or ""))
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

        # ④ 弹确认卡（批量审批）
        tool_call_id = request.tool_call.get("id") or ""

        # 使用 run 级锁防止并发创建多个 interrupt
        lock = await self._get_batch_lock(run_id)
        async with lock:
            # 收集短时间内的多个 read 请求
            pending_reads = await self._collect_pending_reads(run_id, tool_call_id, path, args)

            # 检查是否已有决定（中断恢复重放）
            existing = await find_decided(run_id, tool_call_id)
            if existing is not None:
                return await self._apply(existing, request, handler, tool_name, scope, session_id, path)

            # 构建批量审批 payload
            if len(pending_reads) == 1:
                # 单个请求：保持原有逻辑
                reason = f"AI 想读取{humanize_virtual_path(path)}"
                payload = {
                    "kind": INTERRUPT_KIND,
                    "risk": "read_scope",
                    "reason": reason,
                    "tool_name": tool_name,
                    "args": _truncate_args(args),
                    "tool_call_id": tool_call_id,
                    "decisions": self.allowed_decisions,
                    "batch_size": 1,
                }
            else:
                # 批量请求：合并为一张卡片
                paths_text = "、".join([humanize_virtual_path(p) for _, p, _ in pending_reads])
                reason = f"AI 想读取以下 {len(pending_reads)} 个文件：{paths_text}"
                payload = {
                    "kind": INTERRUPT_KIND,
                    "risk": "read_scope",
                    "reason": reason,
                    "tool_name": tool_name,
                    "args": _truncate_args(args),  # 保留第一个请求的参数
                    "tool_call_id": tool_call_id,
                    "decisions": self.allowed_decisions,
                    "batch_size": len(pending_reads),
                    "batch_paths": [p for _, p, _ in pending_reads],
                    "batch_tool_call_ids": [tc for tc, _, _ in pending_reads],
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
                        "batch_size": payload.get("batch_size", 1),
                    },
                )
            await set_run_status(context, "awaiting_approval")

            decision = interrupt(payload)
            record = await record_decision(run_id, tool_call_id, interrupt_id, decision)

            # 批量审批：如果是 approve_session，需要为所有路径授权
            if decision == "approve_session" and "batch_paths" in payload:
                for batch_path in payload["batch_paths"]:
                    batch_scope = _scope_of(batch_path, project_id)
                    if batch_scope:
                        await read_grants.grant(session_id, batch_scope)

            # 清理已处理的待审批队列
            if run_id in self._pending_reads:
                batch_tool_call_ids = set(payload.get("batch_tool_call_ids", [tool_call_id]))
                self._pending_reads[run_id] = [
                    (tc, p, a, t) for tc, p, a, t in self._pending_reads[run_id]
                    if tc not in batch_tool_call_ids
                ]

        return await self._apply(record, request, handler, tool_name, scope, session_id, path)

    async def _apply(
        self,
        record: dict[str, Any],
        request: Any,
        handler: Any,
        tool_name: str,
        scope: str,
        session_id: str,
        path: str,
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
                f"用户未允许读取该内容（AI 想读取{humanize_virtual_path(path)}）。请改用 outline_grep 工具获取大纲摘录，"
                "或直接在回答里询问用户是否需要读取相关章节。"
            ),
            tool_call_id=tool_call_id,
            name=tool_name,
        )
