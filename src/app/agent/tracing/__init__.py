# -*- coding: utf-8 -*-
"""
模块: app.agent.tracing
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: Langfuse v5（OTel 版）集成（手册「三、(1) 可观测」）。

    实现在 `langfuse.py`（客户端惰性初始化、CallbackHandler、observation、score、trace id）；
    本文件只做 re-export，与 agent/ 其余子包风格一致。
"""

from __future__ import annotations

from app.agent.tracing.langfuse import (
    build_callback_handler,
    create_score,
    current_trace_id,
    get_client,
    observation,
    reset_client,
)

__all__ = [
    "build_callback_handler",
    "create_score",
    "current_trace_id",
    "get_client",
    "observation",
    "reset_client",
]
