# -*- coding: utf-8 -*-
"""
模块: app.agent.tools.registry
文件名: registry.py
作者: xionggaoqing
时间: 2026-10-27
说明: Agent 内置自定义工具装配清单（非工具本体，故不放在 builtin/ 内）。

    ★边界约定（手册「二、(3)」）：**写作业务函数不作为 Agent 工具**（下沉为 REST 接口，
    位于 services/）；Agent 对小说内容的操作一律通过 deepagents 内置文件工具完成
    （ls/read_file/write_file/edit_file/delete/glob/grep 由 FilesystemMiddleware
    按 backend + permissions 自动提供，不在本仓库落文件）。
    因此自定义工具只有不碰路径的 `web_search`（实现在 `builtin/web_search.py`）。
    新增内置工具时在 `build_tools()` 追加，并补充对应测试。
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.builtin.web_search import web_search


def build_tools() -> list[Any]:
    """Agent 内置自定义工具清单（当前仅 web_search）。"""
    return [web_search]


__all__ = ["build_tools", "web_search"]
