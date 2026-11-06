# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.llm.prompt_cache
文件名: prompt_cache.py
作者: xionggaoqing
时间: 2026-11-06
说明: `PromptCacheMiddleware`：显式前缀缓存断点（token 成本治理）。

    隐式前缀缓存（OpenAI 官方等）由 provider 自动判定，无需标记；
    DashScope / Anthropic 支持**显式**断点：在 content 块上带
    `cache_control: {"type": "ephemeral"}`，命中部分按折扣计费且命中率可查
    （usage_metadata.input_token_details.cache_read → 前端头部缓存命中率图标）。

    断点位置（对齐「稳定内容靠前、易变靠尾」的注入顺序，最多 2 个）：
    1. 基础系统提示（跨 run 字节恒定）——跨 run 也命中；
    2. 本轮用户消息（run 内所有模型调用的公共前缀终点）——run 内逐次调用命中
       「system + 全部历史 + 本轮 user」整段。
    尾部注入段（recap/outline_ctx 等）不打断点：它们位置在 user 之后且按 run 变化，
    断点收益低且占用断点配额（DashScope 单请求上限 4）。

    模型未打 `prompt_cache_mode` 标记（隐式缓存 provider）时本中间件原样透传。
    仅做**请求级**改写（`request.override`），不回写 graph state。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 单请求断点上限（DashScope 4 / Anthropic 4），我们只用 2
_MAX_BREAKPOINTS = 2


def _with_cache_control(message: Any) -> Any:
    """把纯文本 content 的消息复制为带 cache_control 断点的 content 块列表。"""
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content:
        return message
    blocks = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
    copy = message.model_copy(update={"content": blocks}) if hasattr(message, "model_copy") else message
    return copy


class PromptCacheMiddleware(AgentMiddleware):
    """给稳定前缀打显式缓存断点（system + 本轮 user），提升缓存命中率。"""

    name = "PromptCache"

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        mode = getattr(request.model, "prompt_cache_mode", None)
        if not mode:
            return await handler(request)

        messages = list(request.messages)
        marked = 0

        # 断点 1：基础系统提示（无 name 的首条 SystemMessage）
        for i, m in enumerate(messages):
            if isinstance(m, SystemMessage) and getattr(m, "name", None) is None:
                messages[i] = _with_cache_control(m)
                marked += 1
                break
        # 断点 2：本轮用户消息（最后一条无 name 的 HumanMessage）
        if marked < _MAX_BREAKPOINTS:
            for i in range(len(messages) - 1, -1, -1):
                m = messages[i]
                if isinstance(m, HumanMessage) and getattr(m, "name", None) is None:
                    messages[i] = _with_cache_control(m)
                    marked += 1
                    break
        if not marked:
            return await handler(request)
        return await handler(request.override(messages=messages))
