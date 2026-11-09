# -*- coding: utf-8 -*-
"""
模块: app.agent.tools.registry
文件名: registry.py
作者: xionggaoqing
时间: 2026-11-09
说明: Agent 内置自定义工具装配清单（非工具本体，故不放在 builtin/ 内）。

    边界约定（手册「二、(3)」）：**写作业务函数原则上不作为 Agent 工具**（下沉为
    REST 接口，位于 services/）；Agent 对小说**正文**的操作一律通过 deepagents
    内置文件工具完成（ls/read_file/write_file/edit_file/delete/glob/grep 由
    FilesystemMiddleware 按 backend + permissions 自动提供，不在本仓库落文件）。

    ★ 已收窄的例外——**卷/章的结构操作**必须是工具（`create_structure.py`）：
    `create_volume` / `create_chapter` / `rename_volume` / `rename_chapter` /
    `delete_volume` / `delete_chapter`。
    原因是结构操作绕不开 `volumes.yaml` 的 id 分配、`sort_order` 顺延、标题校验、
    审计与索引刷新，让 Agent 用 write_file 手搓这些必然出错（实际表现为建不出卷、
    或建出孤儿目录靠自愈兜底；改名则只改了 frontmatter 而卷名不同步；删除则绕过
    回收站归档、修订快照清理、卷内重编号）。
    这六个工具只是 `services/volumes.py` 既有函数的薄封装，业务规则不重复实现，
    且**只碰结构、不碰正文**——正文仍旧走文件工具，由 `FileChangeNotifyMiddleware`
    登记（快照 / version / word_count / 摘要 / 索引）。

    因此自定义工具共九个：不碰路径的 `web_search`、按需摘录大纲的 `outline_grep`
    （渐进式披露，避免整篇读大纲浪费 token）、向用户多问题卡片提问的 `ask_user`
    （思考中间过程需要用户决策时暂停等待），以及上述六个结构工具。
    新增内置工具在 `build_tools()` 追加，并补充对应测试。

    ★ 结构工具的权限分配：
    - `create_volume` / `create_chapter` / `rename_volume` / `rename_chapter`：
      主 Agent + Writer 子代理可用（Writer 负责正文撰写，结构操作与正文紧密相关）；
    - `delete_volume` / `delete_chapter`：只挂主 Agent（删除影响全局目录，需审批）。
    子代理的工具集在 `loop/builder.py` 里按 spec 收窄。
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.builtin.ask_user import ask_user
from app.agent.tools.builtin.create_structure import (
    create_chapter,
    create_volume,
    delete_chapter,
    delete_volume,
    rename_chapter,
    rename_volume,
)
from app.agent.tools.builtin.outline_grep import outline_grep
from app.agent.tools.builtin.web_search import web_search


def build_tools() -> list[Any]:
    """Agent 内置自定义工具清单。"""
    return [
        web_search,
        outline_grep,
        ask_user,
        create_volume,
        create_chapter,
        rename_volume,
        rename_chapter,
        delete_volume,
        delete_chapter,
    ]


__all__ = [
    "ask_user",
    "build_tools",
    "create_chapter",
    "create_volume",
    "delete_chapter",
    "delete_volume",
    "outline_grep",
    "rename_chapter",
    "rename_volume",
    "web_search",
]
