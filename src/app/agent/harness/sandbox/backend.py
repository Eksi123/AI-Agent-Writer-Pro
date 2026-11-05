# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.sandbox.backend
文件名: backend.py
作者: xionggaoqing
时间: 2026-10-27
说明: CompositeBackend 装配：虚拟路径 → 宿主路径（手册「二、(5)」路由表）。

        /projects/       → data/workspaces/     （各小说工作区）
        /skills/user/    → data/skills/         （用户自建技能物化目录，只读）

    （架构优化）：
    - **技能 = 用户自建**：包内不再随包携带写作技能目录，`/skills/builtin/` 路由取消；
      虚拟路径 `/skills/user/` 与 DB 落库的 `/skills/user/{name}` 绑定保持不变；
    - 权限规则 ④（`/skills/**` 读允许/写拒绝）继续覆盖 `/skills/user/`，语义不变。

    Agent 只见虚拟路径；`default=StateBackend()` 承载 conversation_history 等状态文件。
    禁止 LocalShellBackend / LangSmithSandbox（会激活 execute 并与 permissions 冲突）。
"""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

from app.core.paths import data_root

__all__ = ["build_backend"]


def build_backend(session_id: str = "") -> CompositeBackend:
    """装配 CompositeBackend。

    `session_id` 非空时额外挂载 `/skills/session-{sid}/` → `data/session-skills/{sid}/`
    （会话级技能精选目录，见手册「三、(3) 输入窗口」）。
    """
    root = data_root()
    (root / "skills").mkdir(parents=True, exist_ok=True)
    routes: dict[str, Any] = {
        "/projects/": FilesystemBackend(root_dir=root / "workspaces", virtual_mode=True),
        "/skills/user/": FilesystemBackend(root_dir=root / "skills", virtual_mode=True),
    }
    if session_id:
        from app.core.paths import session_skills_dir

        session_dir = session_skills_dir(session_id)
        routes[f"/skills/session-{session_id}/"] = FilesystemBackend(
            root_dir=session_dir, virtual_mode=True
        )
    return CompositeBackend(default=StateBackend(), routes=routes)
