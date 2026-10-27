# -*- coding: utf-8 -*-
"""
模块: app.agent.memory
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 消息树与会话持久化（手册「二、(4) Message 模块」）。

    - 消息树：`messages.parent_id + branch_index`，会话保存 `active_leaf_id`；
    - 「重新生成」= 同父节点下新增兄弟分支；「编辑用户消息后重发」= 新建分支；
    - 构造 LLM 上下文时只取 `root → active_leaf` 的活跃路径；
    - 持久化双轨：业务库（渲染/审计/导出）+ LangGraph `AsyncSqliteSaver`（恢复/HITL 续跑）。

    实现在 `tree.py`；本文件只做 re-export，与 agent/ 其余子包风格一致。
"""

from __future__ import annotations

from app.agent.memory.tree import (
    active_path_to_langchain,
    add_message,
    load_tree,
    set_active_leaf,
    sibling_branches,
    thread_id_of,
)

__all__ = [
    "active_path_to_langchain",
    "add_message",
    "load_tree",
    "set_active_leaf",
    "sibling_branches",
    "thread_id_of",
]
