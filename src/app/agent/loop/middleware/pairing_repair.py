# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.pairing_repair
文件名: pairing_repair.py
作者: xionggaoqing
时间: 2026-11-05
说明: `PairingRepairMiddleware`：工具消息配对自愈（多并行 tool call 错位修复）。

    实测缺陷（2026-11-05 run）：一次 AIMessage 带多个并行 tool_calls 时，
    state 里的 ToolMessage 列表偶发错位——某条结果丢失、另一条结果重复成对
    （如 attachments 读取结果消失、outline 结果出现两次）。模型因此看到
    「重复循环的异常内容」并反复重读附件，token 暴涨。

    本中间件在**每次模型调用前**校验配对：`AIMessage.tool_calls` 的 id 序列
    必须与其后连续 ToolMessage 的 `tool_call_id` 序列一一对应；错位时按位置
    **原地修复**（返回同 id 的替换消息，add_messages 按 id 原地替换）：
    - 交换错位（i/j 互换）→ 交换两条内容；
    - 重复/丢失（期望 id 在更后面已出现，或本条是重复件）→ 该位置替换为
      「结果丢失」占位 ToolMessage，模型据此只重读缺失片段而非整篇。

    修复不增删消息条数、不改历史顺序——`MessageSyncer` 的「追加式」基线
    假设不受影响；替换随 checkpoint 持久化，后续调用不再看到错位上下文。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from app.core.logging import get_logger

logger = get_logger(__name__)


def _lost_placeholder(tool_name: str) -> str:
    return (
        f"（系统修复）{tool_name} 的本次执行结果在上下文同步中丢失。"
        "如仍需要该内容，请用 offset/limit 只重读缺失片段，不要整篇重读。"
    )


def compute_repairs(ai_message: Any, tool_messages: list[Any]) -> list[ToolMessage]:
    """校验一个 tool-call 块的配对，返回需要原地替换的 ToolMessage 列表（空 = 无错位）。"""
    calls = [c for c in (getattr(ai_message, "tool_calls", None) or []) if isinstance(c, dict)]
    expected = [str(c.get("id") or "") for c in calls]
    if not expected or len(tool_messages) != len(expected):
        return []
    actual = [str(getattr(m, "tool_call_id", "") or "") for m in tool_messages]
    if actual == expected:
        return []

    names = [str(c.get("name") or "tool") for c in calls]
    repairs: list[ToolMessage] = []
    for i, want in enumerate(expected):
        if actual[i] == want:
            continue
        j = next((k for k in range(len(expected)) if actual[k] == want), None)
        if j is not None:
            # 交换/前移错位：把该 id 的真实结果搬到正确位置（对侧位置随后
            # 按同样规则归位或落占位）
            content = getattr(tool_messages[j], "content", "")
        else:
            # 结果彻底丢失 / 本条是重复件：占位说明，避免模型把重复内容当原文、
            # 或无休止整篇重读
            content = _lost_placeholder(names[i])
        repairs.append(
            ToolMessage(
                content=content,
                tool_call_id=want,
                name=names[i],
                id=getattr(tool_messages[i], "id", None),
            )
        )
    if repairs:
        logger.warning(
            "tool_pairing_repaired", expected=expected, actual=actual, fixed=len(repairs)
        )
    return repairs


class PairingRepairMiddleware(AgentMiddleware):
    """模型调用前修复 tool_calls 与 ToolMessage 的配对错位。"""

    name = "PairingRepair"

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = list(state.get("messages") or [])
        repairs: list[ToolMessage] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                block: list[Any] = []
                k = i + 1
                while k < len(messages) and isinstance(messages[k], ToolMessage):
                    block.append(messages[k])
                    k += 1
                repairs.extend(compute_repairs(msg, block))
                i = k
            else:
                i += 1
        if not repairs:
            return None
        return {"messages": repairs}
