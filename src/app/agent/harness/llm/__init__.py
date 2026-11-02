# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.llm
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: LLM 容错与预算安全措施。

    - `budget.py`：token 精确/近似计数与预算报告（超阈值告警，RecapInjection 用）；
    - `resilience.py`：重试 / 降级链 / 调用次数上限中间件的**安全参数装配**
      （由 loop/builder 的中间件栈调用；模型解析本体在 loop/llm/factory）。
"""

from app.agent.harness.llm.budget import (
    budget_report,
    count_messages_tokens,
    count_tokens,
    count_tokens_approx,
)
from app.agent.harness.llm.resilience import build_resilience_middleware

__all__ = [
    "budget_report",
    "count_messages_tokens",
    "count_tokens",
    "count_tokens_approx",
    "build_resilience_middleware",
]
