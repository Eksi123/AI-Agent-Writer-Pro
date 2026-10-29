# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 自定义中间件（手册「二、(2)」表，挂载点用真实 API）。

    装配顺序：ModelRetry → ModelFallback → ModelCallLimit → TodoList
    → RecapInjection → SteerInjection → WorkspaceGuard → FileChangeNotify → WriteApproval。
"""

from app.agent.loop.middleware.file_change import FileChangeNotifyMiddleware
from app.agent.loop.middleware.recap import RecapInjectionMiddleware
from app.agent.loop.middleware.steer import SteerInjectionMiddleware
from app.agent.loop.middleware.workspace_guard import WorkspaceGuardMiddleware
from app.agent.loop.middleware.write_approval import WriteApprovalMiddleware

__all__ = [
    "RecapInjectionMiddleware",
    "SteerInjectionMiddleware",
    "WorkspaceGuardMiddleware",
    "FileChangeNotifyMiddleware",
    "WriteApprovalMiddleware",
]
