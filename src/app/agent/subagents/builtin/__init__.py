# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.builtin
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-09
说明: 3 个内置写作子代理的**出厂定义**（手册「二、(3) Sub-agents」）。

    规划（大纲结构/分卷节奏/伏笔布局）与联网检索由主 Agent 亲自完成，
    不再设独立子代理。剩余 3 个执行类子代理：
    - `writer`：大纲与正文的撰写修改**全流程** + 卷章结构操作（建/改名）；
    - `reviewer`：大纲与小说内容的**检查者**（连贯性/结构/匹配度审查）；
    - `recaller`：在隔离上下文提取前情提要/人物状态/伏笔线索并回传摘要，
      避免整份前情提要常驻主上下文。

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
_ORDER = ("writer", "reviewer", "recaller")


def _load(name: str) -> dict[str, Any]:
    """读取子代理目录：spec.SPEC + prompt.md（提示词独立文件）。"""
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
