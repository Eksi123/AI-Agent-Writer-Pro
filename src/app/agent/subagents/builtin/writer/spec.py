# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.builtin.writer
文件名: spec.py
作者: xionggaoqing
时间: 2026-11-09
说明: Writer 子代理出厂定义。

    职责：大纲与小说正文的撰写修改**全流程**（原 writer + editor 合并）：
    按大纲章简介生成正文、续写、扩写，以及去水词/修病句/统一称谓的润色打磨。
    同时负责卷章结构操作（建卷/建章/改名），因为这些操作与正文撰写紧密相关。
    `system_prompt` 由 builtin/__init__.py 从同目录 `prompt.md` 读入。
"""

from __future__ import annotations

from typing import Any

SPEC: dict[str, Any] = {
    "name": "writer",
    "label": "Writer",
    "description": "撰写修改：按大纲章简介生成/续写正文，也做润色校对；负责建卷/建章/改名等结构操作。要生成或打磨正文时委派。",
    "skills": [],
    #: 默认内置工具（按职能）：写正文必须能读大纲/前章并落盘章节文件；
    #: 结构工具（create/rename）用于建卷/建章/改名
    "tools": [
        "read_file", "write_file", "edit_file", "ls", "grep", "glob",
        "create_volume", "create_chapter", "rename_volume", "rename_chapter",
    ],
    #: workflows 绑定：四阶段固定流程的 S2 生成阶段由 writer 执行
    "workflows": ["writing_steps"],
    "mode": "isolated",
}
