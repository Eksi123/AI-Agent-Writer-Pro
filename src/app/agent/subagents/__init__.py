# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-09
说明: 写作子代理配置与构建（手册「二、(3) Sub-agents」，架构优化 ）。

    3 个内置执行类子代理 writer / reviewer / recaller
    （UI 展示名 Writer / Reviewer / Recaller）：
    - `builtin/{name}/`：出厂定义（spec.py）+ 系统提示词（prompt.md，独立文件）；
    - `user/`：用户自建子代理装载（配置数据在 DB，经 services/subagents CRUD）；
    - `builder.py`：SubAgent 装配（模型覆盖 / 提示词覆盖 / 工具收窄 / workflows 绑定查询）。

    规划（大纲结构/分卷节奏/伏笔布局）与联网检索由主 Agent 亲自完成，
    不再设独立子代理。

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
