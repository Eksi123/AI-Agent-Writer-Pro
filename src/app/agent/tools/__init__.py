# -*- coding: utf-8 -*-
"""
模块: app.agent.tools
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 工具与技能（架构优化：tools 与 skills 合并）。

    - `builtin/`：**只放内置工具实现**——`web_search.py`（联网搜索）、
      `outline_recall.py`（按需摘录大纲）。
      文件类工具（ls/read_file/write_file/edit_file/delete/glob/grep）由 deepagents
      FilesystemMiddleware 按 backend + permissions 自动提供，不在本仓库落文件；
      无随包出厂技能目录（技能 = 用户自建）；
    - `registry.py`：内置自定义工具装配清单（`build_tools()`，builder 取用；非工具本体）；
    - `users/`：用户自建技能装载（物化目录 `data/skills/`，经 `/skills/user/` 路由；
      配置数据在 DB `skill_configs`，由 services/skills 维护）。

    边界约定（手册「二、(3)」）：写作业务函数不作为 Agent 工具（下沉为 REST 接口）；
    Agent 对小说内容的操作一律通过 deepagents 内置文件工具完成。
"""

from app.agent.tools.builtin.outline_recall import outline_recall
from app.agent.tools.builtin.web_search import web_search
from app.agent.tools.registry import build_tools

__all__ = ["build_tools", "outline_recall", "web_search"]
