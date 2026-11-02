# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.builtin.reviewer
文件名: spec.py
作者: xionggaoqing
时间: 2026-11-02
说明: Reviewer 子代理出厂定义（架构优化 v1102）。

    职责：大纲和小说内容的**检查者**（原 continuity-checker 扩展）：
    时间线/人物状态/设定冲突/未回收伏笔的连贯性审查，以及大纲结构、
    章简介完整性、正文与简介匹配度的内容审查。只读输出问题清单。
    `system_prompt` 由 builtin/__init__.py 从同目录 `prompt.md` 读入。
"""

from __future__ import annotations

from typing import Any

SPEC: dict[str, Any] = {
    "name": "reviewer",
    "label": "Reviewer",
    "description": "审查者：核查大纲结构与章简介完整性、时间线/人物状态/设定冲突/未回收伏笔，以及正文与大纲简介的匹配度。需要检查一致性或内容质量时委派给它。",
    "skills": [],
    #: 默认内置工具（按职能）：只读审查——检索证据不需要写工具
    "tools": ["read_file", "ls", "grep", "glob"],
    #: workflows 绑定：流程 S1/S3 校验 + 全书一致性审查同域
    "workflows": ["writing_steps", "book_consistency"],
    "mode": "isolated",
}
