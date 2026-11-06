# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.scrub
文件名: scrub.py
作者: xionggaoqing
时间: 2026-11-06
说明: `SubagentContextScrubMiddleware`：子代理**用完即销毁**其上下文。

    子代理（mode=isolated）的中间消息原本只活在它自己的 ainvoke 局部 state 里，
    但 deepagents 的 `task` 工具会把子代理**完整结果 state** 回传主图——
    其中除 messages 外的私有键、以及任何后续扩展键都可能携带子代理上下文。

    本中间件挂在每个子代理的 middleware 末尾，在子代理**结束时**（aafter_agent）
    把 state 的 messages 原地替换为**仅最终结论**（最后一条非空文本 AIMessage）：
    - 回传主图的结果 state 只含结论，子代理中间过程（读到的正文/摘要/检索片段）
      不进入主 Agent 上下文、不落任何持久化；
    - `task` 工具提取回传内容依赖「最后一条非空 AIMessage」，scrub 后它仍在，
      回传语义不变；
    - 子代理图对象随 run 结束释放，无残留引用。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from app.core.logging import get_logger

logger = get_logger(__name__)


def final_conclusion_of(messages: list[Any]) -> Any | None:
    """取子代理的最终结论：最后一条非空文本 AIMessage；无则最后一条 AIMessage。"""
    last_ai: Any | None = None
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            text = msg.text if getattr(msg, "text", None) is not None else str(msg.content or "")
            if text.strip():
                return msg
            last_ai = last_ai or msg
    return last_ai


class SubagentContextScrubMiddleware(AgentMiddleware):
    """子代理结束时销毁中间上下文，state 只留最终结论。"""

    name = "SubagentContextScrub"

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        final = final_conclusion_of(messages)
        if final is None:
            return None
        if len(messages) == 1:
            return None  # 只有结论本身，无需清理
        logger.debug("subagent_context_scrubbed", dropped=len(messages) - 1)
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), final]}


__all__ = ["SubagentContextScrubMiddleware", "final_conclusion_of"]
