# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.builtin.planner
文件名: spec.py
作者: xionggaoqing
时间: 2026-11-02
说明: Planner 子代理出厂定义（架构优化 v1102）。

    职责：题材定位、三级大纲、分卷节奏、伏笔布局。
    `system_prompt` 由 builtin/__init__.py 从同目录 `prompt.md` 读入，此处不内联。
"""

from __future__ import annotations

from typing import Any

SPEC: dict[str, Any] = {
    "name": "planner",
    #: UI 展示名（deepagents name 只允许小写字母数字连字符）
    "label": "Planner",
    "description": "策划：题材定位、三级大纲、分卷节奏、伏笔布局。需要规划故事结构或拆解章节要点时委派给它。",
    #: 技能 = 用户自建（经 /skills/user/ 绑定）；出厂不预绑
    "skills": [],
    #: 默认内置工具（按职能）：策划要读现有大纲设定、并把规划落盘到大纲文件
    "tools": ["read_file", "write_file", "edit_file", "ls", "grep", "glob"],
    #: workflows 绑定：planner 产出的大纲是四阶段固定流程 S0/S1 的输入
    "workflows": ["writing_steps"],
    "mode": "isolated",
}
