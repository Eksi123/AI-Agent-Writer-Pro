# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.builtin.web_searcher
文件名: spec.py
作者: xionggaoqing
时间: 2026-11-02
说明: Web_Searcher 子代理出厂定义（架构优化 v1102，原 researcher）。

    职责：通过 web_search 或第三方 MCP 获取外部资料（历史、地理、术语、文化等）。
    子代理 name 为 `web-searcher`（deepagents 命名规则不允许下划线/大写），
    UI 展示名 Web_Searcher（label）。目录名用下划线（Python 模块名限制）。
    `system_prompt` 由 builtin/__init__.py 从同目录 `prompt.md` 读入。
"""

from __future__ import annotations

from typing import Any

SPEC: dict[str, Any] = {
    "name": "web-searcher",
    "label": "Web_Searcher",
    "description": "资料检索：通过 web_search 或第三方 MCP 获取外部资料（历史、地理、术语、文化等）。需要查证真实世界知识时委派给它。",
    "skills": [],
    #: 默认内置工具（按职能）：只检索外部资料，不给文件写工具
    "tools": ["web_search"],
    "workflows": [],
    "mode": "isolated",
}
