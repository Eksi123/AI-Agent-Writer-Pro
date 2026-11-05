# -*- coding: utf-8 -*-
"""
模块: app.agent.more.langfuse
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: Langfuse v5（OTel 版）观测集成（架构优化 观测属 More）。

    实现在 `langfuse.py`（客户端惰性初始化、CallbackHandler、observation、score、trace id）；
    未配置 Key 时全部自动降级为 no-op，不影响 Agent 运行。
"""

from __future__ import annotations

from app.agent.more.langfuse.langfuse import (
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
