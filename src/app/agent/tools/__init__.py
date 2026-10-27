#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.tools
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: Agent 内置工具：只有少量不碰路径的工具（写作业务函数**不注册为工具**）。

    - `web_search.py`：联网搜索工具实现（Wikipedia + DuckDuckGo 免 Key 双源，
      源不可用必须返回**明确错误**而非静默空结果）；
    - `registry.py`：内置工具装配清单（builder 经 `build_tools()` 取用）。
"""

from app.agent.tools.registry import build_tools
from app.agent.tools.web_search import web_search

__all__ = ["build_tools", "web_search"]
