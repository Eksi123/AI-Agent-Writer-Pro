# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.guard
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 工作区守卫：`WorkspaceGuardMiddleware`（路径校验第二道 + 审计埋点 +
    错误指纹记忆与阻断）。

    与 harness/sandbox permissions 的关系：permissions 只作用于 deepagents
    内置文件系统工具（第一道）；本守卫覆盖自定义工具/MCP（第二道），
    并负责耗时/结果大小统计与重复错误阻断（同一错误 2 次告警、3 次阻断）。
"""

from app.agent.harness.guard.workspace_guard import WorkspaceGuardMiddleware

__all__ = ["WorkspaceGuardMiddleware"]
