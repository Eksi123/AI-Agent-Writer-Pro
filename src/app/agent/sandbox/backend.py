# -*- coding: utf-8 -*-
"""
模块: app.agent.sandbox.backend
文件名: backend.py
作者: xionggaoqing
时间: 2026-10-27
说明: CompositeBackend 装配：虚拟路径 → 宿主路径（手册「二、(5)」路由表）。

        /projects/       → data/workspaces/     （各小说工作区）
        /skills/builtin/ → src/app/agent/skills/（内置技能，只读）
        /skills/user/    → data/skills/         （用户自建技能物化目录，只读）

    Agent 只见虚拟路径；`default=StateBackend()` 承载 conversation_history 等状态文件。
    禁止 LocalShellBackend / LangSmithSandbox（会激活 execute 并与 permissions 冲突）。
"""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

from app.core.paths import BUILTIN_SKILLS_DIR, data_root

__all__ = ["BUILTIN_SKILLS_DIR", "build_backend"]


def build_backend(session_id: str = "") -> CompositeBackend:
    """装配 CompositeBackend。

    `session_id` 非空时额外挂载 `/skills/session/` → `data/session-skills/{session_id}/`
    （会话级技能精选目录，见手册「三、(3) 输入窗口」）。
    """
    root = data_root()
    (root / "skills").mkdir(parents=True, exist_ok=True)
    BUILTIN_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    routes: dict[str, Any] = {
        "/projects/": FilesystemBackend(root_dir=root / "workspaces", virtual_mode=True),
        "/skills/builtin/": FilesystemBackend(root_dir=BUILTIN_SKILLS_DIR, virtual_mode=True),
        "/skills/user/": FilesystemBackend(root_dir=root / "skills", virtual_mode=True),
    }
    if session_id:
        from app.core.paths import session_skills_dir

        session_dir = session_skills_dir(session_id)
        routes[f"/skills/session-{session_id}/"] = FilesystemBackend(
            root_dir=session_dir, virtual_mode=True
        )
    return CompositeBackend(default=StateBackend(), routes=routes)
