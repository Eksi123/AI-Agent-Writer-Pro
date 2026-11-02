# -*- coding: utf-8 -*-
"""
模块: app.agent.tools.users
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 用户自建技能装载（架构优化 v1102）。

    用户技能的**配置数据**存 DB `skill_configs`（经 services/skills CRUD 维护），
    并**物化**到 `data/skills/{name}/SKILL.md`，由 harness/sandbox/backend 经虚拟路径
    `/skills/user/` 挂载供 Agent 只读加载（渐进披露：模型需要时自己 read_file）。

    本模块只提供 Agent 侧的装载辅助：物化目录枚举与校验。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.paths import skills_root


def user_skill_dirs() -> list[Path]:
    """已物化的用户技能目录列表（`data/skills/{name}/`，含 SKILL.md 者）。"""
    root = skills_root()
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())


def user_skill_names() -> list[str]:
    """已物化的用户技能名列表。"""
    return [d.name for d in user_skill_dirs()]


def user_skill_assets() -> dict[str, Any]:
    """技能名 → SKILL.md 路径（装配诊断用）。"""
    return {d.name: str(d / "SKILL.md") for d in user_skill_dirs()}
