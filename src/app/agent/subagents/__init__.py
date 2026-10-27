# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 写作子代理配置与构建（手册「二、(3) Sub-agents」）。

    5 个内置子代理：planner / writer / editor / continuity-checker / researcher。
    每个可配置模型覆盖（model_ref）、专属提示词、工具白名单、可用技能、独立权限、
    是否审批（interrupt_on）、上下文模式（mode，默认 isolated；fork 禁用 skills）。
    `general-purpose` 由 deepagents 在传入 subagents 时自动追加。

    实现分布：`builtin.py`（出厂定义数据）+ `builder.py`（SubAgent 装配）。
    本文件只做 re-export，与 agent/ 其余子包风格一致。
"""

from __future__ import annotations

from app.agent.subagents.builder import build_subagents
from app.agent.subagents.builtin import BUILTIN_SUBAGENTS, builtin_subagent_names

__all__ = ["BUILTIN_SUBAGENTS", "build_subagents", "builtin_subagent_names"]
