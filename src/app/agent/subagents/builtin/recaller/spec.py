# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.builtin.recaller
文件名: spec.py
作者: xionggaoqing
时间: 2026-11-05
说明: Recaller 子代理出厂定义（前情提要按需提取，不常驻主上下文）。

    职责：在**隔离上下文**里回顾已写内容并回传精炼结论——主 Agent 不再默认
    注入整份前情提要（每轮模型调用都重发，token 成本高），需要回顾时才派发本子代理。
    工具只读：摘要优先（`summaries/`，字数远少于正文），正文按需 grep 定位后节选读取。
    `system_prompt` 由 builtin/__init__.py 从同目录 `prompt.md` 读入。
"""

from __future__ import annotations

from typing import Any

SPEC: dict[str, Any] = {
    "name": "recaller",
    "label": "Recaller",
    "description": "回顾者：提取前情提要、人物状态与伏笔线索，只回传摘要。要回顾已写内容时委派。",
    "skills": [],
    #: 只读回顾：摘要/正文检索 + 大纲按需摘录 + ask_user 不确定时询问用户，不给写工具
    "tools": ["read_file", "grep", "glob", "outline_grep", "ask_user"],
    "workflows": [],
    "mode": "isolated",
}
