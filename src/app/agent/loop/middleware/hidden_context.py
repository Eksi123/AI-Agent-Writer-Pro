# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.hidden_context
文件名: hidden_context.py
作者: xionggaoqing
时间: 2026-11-01
说明: ★v1101：`HiddenContextInjectionMiddleware`——把「隐藏上下文」（如选区润色提示词）
    以**请求级** system 消息注入模型调用（`request.override(messages=...)`）。

    与 RecapInjection 同机制：只影响本次模型调用，不回写 graph state、不入消息树，
    因此会话里看不到这段提示词，但模型每一轮调用都能读到它。
    内容由 `run_manager` 按 run_id 持有（发消息时经 `hidden_context` 字段传入）。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

_MARK = "hidden_ctx"


class HiddenContextInjectionMiddleware(AgentMiddleware):
    """请求级注入 run 级隐藏上下文（system 消息，name=hidden_ctx）。"""

    name = "HiddenContextInjection"

    def __init__(self, provider: Any = None) -> None:
        """`provider`：`(run_id) -> str`，返回该 run 的隐藏上下文（空串 = 不注入）。"""
        super().__init__()
        self._provider = provider

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        context = getattr(request.runtime, "context", None)
        run_id = getattr(context, "run_id", "") or ""
        text = ""
        if self._provider is not None and run_id:
            try:
                text = self._provider(run_id) or ""
            except Exception as exc:  # pragma: no cover - 注入失败不阻断运行
                logger.warning("hidden_context_provider_failed", run_id=run_id, error=str(exc))
        if not text:
            return await handler(request)

        messages = list(request.messages)
        injected = SystemMessage(content=text, name=_MARK)
        if any(getattr(m, "name", None) == _MARK for m in messages):
            messages = [injected if getattr(m, "name", None) == _MARK else m for m in messages]
        else:
            messages = [injected, *messages]
        return await handler(request.override(messages=messages))
