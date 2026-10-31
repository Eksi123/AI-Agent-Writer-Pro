# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.recap
文件名: recap.py
作者: xionggaoqing
时间: 2026-10-27
说明: `RecapInjectionMiddleware`：`abefore_model` 注入前情提要 + token 预算检查。

    手册「二、(2)」：注入前情提要、token 预算检查（超阈值先触发裁剪）；
    敏感内容检查**仅预留挂载位，MVP 不实现**。

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
    """在每次模型调用前，把前情提要追加到 system 提示。"""

    name = "RecapInjection"

    def __init__(self, recap_provider: Any = None) -> None:
        """`recap_provider`：`async (context) -> str`，返回前情提要文本。"""
        super().__init__()
        self._recap_provider = recap_provider

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        context = getattr(runtime, "context", None)
        recap = await self._build_recap(context)
        if not recap:
            return None

        # 敏感内容检查挂载位（MVP 不实现，见手册「二、(2)」）
        self._check_sensitive(state)

        messages = list(state.get("messages") or [])
        if not messages:
            return None
        # 前情提要作为 system 消息插到最前（deepagents summarization 会保留首个 system）

        injected = SystemMessage(content=recap, name="recap")
        existing = [m for m in messages if getattr(m, "name", None) == "recap"]
        if existing:
            messages = [injected if getattr(m, "name", None) == "recap" else m for m in messages]
        else:
            messages = [injected, *messages]
        return {"messages": messages}

    async def _build_recap(self, context: Any) -> str:
        if self._recap_provider is None:
            return ""
        try:
            return await self._recap_provider(context) or ""
        except Exception as exc:  # 前情提要失败不阻断生成
            logger.warning("recap_build_failed", error=str(exc))
            return ""

    @staticmethod
    def _check_sensitive(_state: Any) -> None:
        """敏感内容检查（预留挂载位，MVP 不实现）。"""
        return None

    async def awrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        """token 预算检查：超阈值记录告警（M4 接入实际裁剪）。"""
        context = getattr(request.runtime, "context", None)
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
        return await handler(request)
