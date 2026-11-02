# -*- coding: utf-8 -*-
"""
模块: app.agent.tools.builtin
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 内置工具实现目录（只放工具本体）：
    - `web_search.py`：联网搜索（Wikipedia + DuckDuckGo 免 Key 双源，
      源不可用返回明确错误而非静默空结果）。

    文件类工具（ls/read_file/write_file/edit_file/delete/glob/grep）由 deepagents
    FilesystemMiddleware 提供，不在本目录落文件；装配清单在上级 `tools/registry.py`。
"""

from app.agent.tools.builtin.web_search import web_search

__all__ = ["web_search"]
