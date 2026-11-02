# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.llm
文件名: resilience.py
作者: xionggaoqing
时间: 2026-11-02
说明: LLM 容错中间件装配（自 loop/builder.build_middleware_stack 拆出，架构优化 v1102）。

    三段安全措施（顺序即语义，由调用方置于中间件栈前部）：
    - ModelRetry：仅对超时 / 429 / 5xx / 连接错误重试（指数退避 + 抖动）；
    - ModelFallback：主模型失败按降级链依次切换（链在 loop/resolve.resolve_models 解析）；
    - ModelCallLimit：单次 run 的 LLM 调用软上限，超限**优雅收尾**（exit_behavior="end"）
      而非硬断——防止 Agent 死循环烧 token（硬上限另经 config recursion_limit）。

    装配容错：任一中间件在当前 langchain 版本不可用时告警跳过，不阻断装配
    （与既有 builder 的兼容性兜底行为一致）。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 单次 run 的 LLM 调用软上限默认值（可被 settings.agent_model_call_limit 覆盖）
DEFAULT_MODEL_CALL_LIMIT = 40


def build_resilience_middleware(
    *,
    primary_model: BaseChatModel,
    fallback_models: list[BaseChatModel],
    model_call_limit: int = DEFAULT_MODEL_CALL_LIMIT,
) -> list[Any]:
    """装配 重试 → 降级 → 限次 三段中间件（顺序固定）。"""
    stack: list[Any] = []

    # ① 重试：仅对超时 / 429 / 5xx / 连接错误重试（默认 retry_on 即覆盖这些）
    try:
        stack.append(ModelRetryMiddleware(max_retries=2, backoff_factor=2.0, jitter=True))
    except Exception as exc:  # pragma: no cover - 参数兼容性兜底
        logger.warning("model_retry_mw_fallback", error=str(exc))
        stack.append(ModelRetryMiddleware())

    # ② 降级链：主模型失败依次切换
    if fallback_models:
        try:
            from langchain.agents.middleware import ModelFallbackMiddleware

            stack.append(ModelFallbackMiddleware(primary_model, *fallback_models))
        except Exception as exc:  # pragma: no cover
            logger.warning("model_fallback_mw_unavailable", error=str(exc))

    # ③ 调用次数上限：优雅收尾
    try:
        stack.append(ModelCallLimitMiddleware(run_limit=model_call_limit, exit_behavior="end"))
    except Exception as exc:  # pragma: no cover
        logger.warning("model_call_limit_mw_unavailable", error=str(exc))

    return stack
