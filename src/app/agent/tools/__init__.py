# -*- coding: utf-8 -*-
"""
模块: app.agent.tools
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 工具与技能（架构优化：tools 与 skills 合并）。

    - `builtin/`：**只放内置工具实现**——`web_search.py`（联网搜索）、
      `outline_grep.py`（按需摘录大纲）、`ask_user.py`（多问题卡片提问）、
      `create_structure.py`（卷/章的建与改名，共四个工具）。
      文件类工具（ls/read_file/write_file/edit_file/delete/glob/grep）由 deepagents
      FilesystemMiddleware 按 backend + permissions 自动提供，不在本仓库落文件；
      无随包出厂技能目录（技能 = 用户自建）；
    - `registry.py`：内置自定义工具装配清单（`build_tools()`，builder 取用；非工具本体）；
    - `users/`：用户自建技能装载（物化目录 `data/skills/`，经 `/skills/user/` 路由；
      配置数据在 DB `skill_configs`，由 services/skills 维护）。

    边界约定（手册「二、(3)」）：写作业务函数**原则上**不作为 Agent 工具（下沉为
    REST 接口）；Agent 对小说**正文**的操作一律通过 deepagents 内置文件工具完成。
    已收窄的例外是卷/章的**结构操作**（建卷、建章、改名）——它们绕不开
    `volumes.yaml` 的 id 分配、`sort_order` 顺延、标题校验、审计与索引刷新，
    让 Agent 用 write_file 手搓必然出错，故以 `create_structure.py` 的四个薄封装
    工具提供（只碰结构、不碰正文）。详见 `registry.py` 的模块说明。
"""

from app.agent.tools.builtin.ask_user import ask_user
from app.agent.tools.builtin.create_structure import (
    create_chapter,
    create_volume,
    rename_chapter,
    rename_volume,
)
from app.agent.tools.builtin.outline_grep import outline_grep
from app.agent.tools.builtin.web_search import web_search
from app.agent.tools.registry import build_tools

__all__ = [
    "ask_user",
    "build_tools",
    "create_chapter",
    "create_volume",
    "outline_grep",
    "rename_chapter",
    "rename_volume",
    "web_search",
]
