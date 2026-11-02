# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.builtin
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 4 个内置写作子代理的**出厂定义**（架构优化 v1102，手册「二、(3) Sub-agents」）。

    ★v1102：旧 5 个（planner/writer/editor/continuity-checker/researcher）整合为 4 个：
    - `planner`：策划（三级大纲、分卷节奏、伏笔布局）；
    - `writer`：大纲与正文的撰写修改**全流程**（原 writer + editor 合并）；
    - `reviewer`：大纲与小说内容的**检查者**（原 continuity-checker 扩展）；
    - `web-searcher`：资料检索（原 researcher，UI 展示名 Web_Searcher）。

    每个子代理一个目录：`spec.py`（结构化定义）+ `prompt.md`（系统提示词，独立文件便于
    版本化与「恢复默认」）。本包在导入期聚合为 `BUILTIN_SUBAGENTS`（name → spec dict，
    spec 内含已读入的 system_prompt）。

    首次启动由 `services.subagents.seed_builtins` 播种进 `subagent_configs`
    （is_builtin=True，可停用/改提示词/改模型覆盖，不可删除）。
    services 侧经 `core.catalog.builtin_subagents()` 惰性读取（import 路径不变）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_BUILTIN_DIR = Path(__file__).resolve().parent

#: 出厂顺序（= 播种与 UI 展示顺序）
_ORDER = ("planner", "writer", "reviewer", "web-searcher")


def _load(name: str) -> dict[str, Any]:
    """读取子代理目录：spec.SPEC + prompt.md（提示词独立文件）。

    目录名用下划线（Python 模块名不允许连字符），子代理 name 用连字符
    （deepagents 命名规则 `^[a-z][a-z0-9-]{0,63}$`）：`web-searcher` ↔ `web_searcher/`。
    """
    import importlib

    mod_name = name.replace("-", "_")
    mod = importlib.import_module(f"app.agent.subagents.builtin.{mod_name}.spec")
    spec: dict[str, Any] = dict(mod.SPEC)
    prompt_file = _BUILTIN_DIR / mod_name / "prompt.md"
    spec["system_prompt"] = prompt_file.read_text(encoding="utf-8").strip()
    return spec


#: 内置子代理定义（name → 描述/提示词/技能/工具/workflows 绑定/mode）
BUILTIN_SUBAGENTS: dict[str, dict[str, Any]] = {name: _load(name) for name in _ORDER}


def builtin_subagent_names() -> list[str]:
    """内置子代理名列表（顺序即出厂顺序）。"""
    return list(_ORDER)
