# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 写作子代理配置与构建（手册「二、(3) Sub-agents」，架构优化 ）。

    4 个内置子代理 planner / writer / reviewer / web-searcher
    （UI 展示名 Planner / Writer / Reviewer / Web_Searcher）：
    - `builtin/{name}/`：出厂定义（spec.py）+ 系统提示词（prompt.md，独立文件）；
    - `user/`：用户自建子代理装载（配置数据在 DB，经 services/subagents CRUD）；
    - `builder.py`：SubAgent 装配（模型覆盖 / 提示词覆盖 / 工具收窄 / workflows 绑定查询）。

    每个可配置模型覆盖（model_ref）、专属提示词、工具白名单、可用技能、
    上下文模式（mode，默认 isolated；fork 禁用 skills）。
    `general-purpose` 由 deepagents 在传入 subagents 时自动追加。
"""

from __future__ import annotations

from app.agent.subagents.builder import build_subagents, workflows_of
from app.agent.subagents.builtin import BUILTIN_SUBAGENTS, builtin_subagent_names

__all__ = [
    "BUILTIN_SUBAGENTS",
    "build_subagents",
    "builtin_subagent_names",
    "workflows_of",
]
