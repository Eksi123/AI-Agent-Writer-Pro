# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.llm
文件名: resilience.py
作者: xionggaoqing
时间: 2026-11-02
说明: LLM 容错中间件装配（自 loop/builder.build_middleware_stack 拆出）。

    三段安全措施（顺序即语义，由调用方置于中间件栈前部）：
    - ModelRetry：仅对超时 / 429 / 5xx / 连接错误重试（指数退避 + 抖动）；
    - ModelSwitchOnFailure：**当前模型连续失败
      达到阈值**才自动切换到其它已启用模型重试（循环一轮仍失败则抛错）；
      不再按预设降级链「一次失败即切换」；
    - ModelCallLimit：单次 run 的 LLM 调用软上限，超限**优雅收尾**（exit_behavior="end"）
      而非硬断——防止 Agent 死循环烧 token（硬上限另经 config recursion_limit）。

    装配容错：任一中间件在当前 langchain 版本不可用时告警跳过，不阻断装配
    （与既有 builder 的兼容性兜底行为一致）。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 单次 run 的 LLM 调用软上限默认值（可被 settings.agent_model_call_limit 覆盖）
DEFAULT_MODEL_CALL_LIMIT = 40

#: 当前模型**连续**失败多少次后切换到下一个模型
DEFAULT_SWITCH_THRESHOLD = 3


class ModelSwitchOnFailureMiddleware(AgentMiddleware):
    """模型连续失败达阈值才切换备用模型（取代旧「降级链一次失败即切换」）。

    - 候选 = [主模型, *alternates]（alternates = 其它已启用模型，resolve 阶段解析）；
    - 每次模型调用抛异常计一次连续失败；成功即清零；
    - 连续失败 ≥ 阈值且还有其它候选 → 循环切到下一个候选并**立即重试**；
    - 一轮候选全部切换过仍失败 → 抛出最后一次异常（交由外层 Retry / 错误收敛）。
    """

    name = "ModelSwitchOnFailure"

    def __init__(self, alternates: list[BaseChatModel], *, fail_threshold: int = DEFAULT_SWITCH_THRESHOLD) -> None:
        super().__init__()
        self._alternates = list(alternates or [])
        self._threshold = max(1, int(fail_threshold))
        self._models: list[BaseChatModel] = []
        self._index = 0
        self._fails = 0

    def _model_name(self, model: Any) -> str:
        return str(getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__)

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        if not self._models:
            self._models = [request.model, *self._alternates]
        if len(self._models) < 2:
            # 没有备用模型 → 不做切换，原样调用（失败由外层 Retry / 错误收敛处理）
            return await handler(request)

        switched = 0
        while True:
            model = self._models[self._index]
            req = request if self._index == 0 else request.override(model=model)
            try:
                result = await handler(req)
                self._fails = 0
                return result
            except Exception as exc:
                self._fails += 1
                if self._fails < self._threshold:
                    raise
                switched += 1
                if switched > len(self._models):
                    # 一轮候选全部试过仍连续失败 → 抛错收尾
                    raise
                prev = self._model_name(model)
                self._index = (self._index + 1) % len(self._models)
                self._fails = 0
                logger.warning(
                    "model_switched_after_consecutive_failures",
                    failed_model=prev,
                    next_model=self._model_name(self._models[self._index]),
                    threshold=self._threshold,
                    error=str(exc)[:200],
                )


def build_resilience_middleware(
    *,
    primary_model: BaseChatModel,
    alternate_models: list[BaseChatModel],
    model_call_limit: int = DEFAULT_MODEL_CALL_LIMIT,
) -> list[Any]:
    """装配 重试 → 连续失败切换 → 限次 三段中间件（顺序固定）。"""
    stack: list[Any] = []

    # ① 重试：仅对超时 / 429 / 5xx / 连接错误重试（默认 retry_on 即覆盖这些）
    try:
        stack.append(ModelRetryMiddleware(max_retries=2, backoff_factor=2.0, jitter=True))
    except Exception as exc:  # pragma: no cover - 参数兼容性兜底
        logger.warning("model_retry_mw_fallback", error=str(exc))
        stack.append(ModelRetryMiddleware())

        # ② 连续失败达阈值才切换备用模型（无预设降级链）
    try:
        stack.append(ModelSwitchOnFailureMiddleware(alternate_models))
    except Exception as exc:  # pragma: no cover
        logger.warning("model_switch_mw_unavailable", error=str(exc))

    # ③ 调用次数上限：优雅收尾
    try:
        stack.append(ModelCallLimitMiddleware(run_limit=model_call_limit, exit_behavior="end"))
    except Exception as exc:  # pragma: no cover
        logger.warning("model_call_limit_mw_unavailable", error=str(exc))

    return stack
