# -*- coding: utf-8 -*-
"""
模块: app.agent.tracing
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: Agent 运行消息与日志的记录、归类、处理和传递（架构优化 v1102）。

    子包：
    - `sse/`：SSE 事件总线（环形缓冲 + seq 续传）与 Emitter（run 级发布快捷方式）；
    - `messages/`：消息树（tree.py，parent_id + branch_index）与 graph state → 业务库
      增量同步（message_sync.py）。

    ★v1102：Langfuse 观测 API 已迁至 `app.agent.more.langfuse`（观测属 More），
    全部调用方（loop/workflows/services/tests）已改用新路径，本包不再转发。
"""

from __future__ import annotations

from app.agent.tracing.messages.tree import (
    active_path_to_langchain,
    add_message,
    history_messages_for_seed,
    load_tree,
    set_active_leaf,
    sibling_branches,
    thread_id_of,
)
from app.agent.tracing.sse.emitter import Emitter
from app.agent.tracing.sse.events import RunStream, SseBus, SseEvent, buffer_lost_event, sse_bus

__all__ = [
    # 消息树
    "active_path_to_langchain",
    "add_message",
    "history_messages_for_seed",
    "load_tree",
    "set_active_leaf",
    "sibling_branches",
    "thread_id_of",
    # SSE
    "Emitter",
    "RunStream",
    "SseBus",
    "SseEvent",
    "buffer_lost_event",
    "sse_bus",
]
