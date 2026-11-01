# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.recap
文件名: recap.py
作者: xionggaoqing
时间: 2026-10-27
说明: `RecapInjectionMiddleware`：`awrap_model_call` 请求级注入前情提要 + token 预算检查。

    手册「二、(2)」：注入前情提要、token 预算检查（超阈值先触发裁剪）；
    敏感内容检查**仅预留挂载位，MVP 不实现**。

    ★v1101：注入走 `request.override(messages=...)`（只影响本次模型调用），
    不再回写 graph state——避免 `add_messages` 重放导致历史消息逐轮重复。

    M3 阶段：注入项目元信息 + 会话上下文章节摘要（`build_recap`）；
    M4 接入大纲章简介与 token 预算裁剪。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)


class RecapInjectionMiddleware(AgentMiddleware):
    """在每次模型调用前，把前情提要追加到 system 提示。

    ★v1101：注入改为**请求级**（`awrap_model_call` 里 `request.override(messages=...)`），
    不再通过 `abefore_model` 返回 state 更新——后者在 LangGraph `add_messages` 语义下
    会把整份 messages 列表当作「新增」重放，导致历史消息在 graph state 里逐轮重复，
    进而被 MessageSyncer 落库、前端渲染成「重复的问答」。请求级注入只影响本次模型调用，
    不写 state、不落消息树。
    """

    name = "RecapInjection"

    def __init__(self, recap_provider: Any = None) -> None:
        """`recap_provider`：`async (context) -> str`，返回前情提要文本。"""
        super().__init__()
        self._recap_provider = recap_provider
        self._cache: dict[str, str] = {}

    async def awrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        context = getattr(request.runtime, "context", None)
        recap = await self._build_recap(context)

        # token 预算检查：超阈值记录告警（M4 接入实际裁剪）
        if context is not None:
            emit = getattr(context, "emit", None)
            if emit is not None:
                try:
                    from app.agent.llm.budget import count_messages_tokens

                    tokens = count_messages_tokens(list(request.messages))
                    report = emit.budget_report(tokens)
                    if report.get("over_budget"):
                        await emit.publish(
                            "context.compact",
                            {
                                "before_tokens": tokens,
                                "after_tokens": tokens,
                                "reason": "over_budget_no_trim_in_m3",
                            },
                        )
                except Exception as exc:  # pragma: no cover
                    logger.debug("budget_check_failed", error=str(exc))

        if not recap:
            return await handler(request)

        # 敏感内容检查挂载位（MVP 不实现，见手册「二、(2)」）
        self._check_sensitive(request)

        messages = list(request.messages)
        injected = SystemMessage(content=recap, name="recap")
        existing = [m for m in messages if getattr(m, "name", None) == "recap"]
        if existing:
            messages = [injected if getattr(m, "name", None) == "recap" else m for m in messages]
        else:
            messages = [injected, *messages]
        # ★请求级注入：只改本次调用的 messages，不回写 graph state
        return await handler(request.override(messages=messages))

    async def _build_recap(self, context: Any) -> str:
        if self._recap_provider is None:
            return ""
        # 同一 run 内前情提要不变：缓存避免每次模型调用重复构建/读文件
        run_id = getattr(context, "run_id", "") or ""
        if run_id and run_id in self._cache:
            return self._cache[run_id]
        try:
            recap = await self._recap_provider(context) or ""
        except Exception as exc:  # 前情提要失败不阻断生成
            logger.warning("recap_build_failed", error=str(exc))
            return ""
        if run_id:
            self._cache[run_id] = recap
        return recap

    @staticmethod
    def _check_sensitive(_request: Any) -> None:
        """敏感内容检查（预留挂载位，MVP 不实现）。"""
        return None
