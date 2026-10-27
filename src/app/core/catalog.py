# -*- coding: utf-8 -*-
"""
模块: app.core.catalog
文件名: catalog.py
作者: xionggaoqing
时间: 2026-10-27
说明: Agent 内置目录数据的**惰性访问器**（消除 services → agent 的模块顶层反向依赖）。

    内置子代理定义（描述/提示词/技能/mode）是 Agent 运行语义数据，按项目约定
    **不外迁出 `agent/`**（数据留在 `agent/subagents/builtin.py`）。
    但 `services/subagents.py` 需要它来播种与「恢复出厂配置」，
    若在服务层顶层 `from app.agent...` 就形成反向依赖。

    这里提供函数级惰性读取：core 不在模块加载期触碰 agent，
    仅在**运行期**（调用时）解析，因而 import 图仍是无环的。
"""

from __future__ import annotations

from typing import Any


def builtin_subagents() -> dict[str, dict[str, Any]]:
    """内置子代理出厂定义（name → description/system_prompt/skills/mode）。"""
    from app.agent.subagents.builtin import BUILTIN_SUBAGENTS

    return BUILTIN_SUBAGENTS


def builtin_subagent_names() -> list[str]:
    """内置子代理名列表（顺序即出厂顺序）。"""
    from app.agent.subagents.builtin import builtin_subagent_names as _names

    return _names()
