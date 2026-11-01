# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 自定义中间件（手册「二、(2)」表，挂载点用真实 API）。

    装配顺序：WriteApproval（v1101 最外层）→ ModelRetry → ModelFallback → ModelCallLimit → TodoList
    → RecapInjection → HiddenContext（v1101）→ SteerInjection → WorkspaceGuard
    → FileChangeNotify → ContentLock（v1101）。
"""

from app.agent.loop.middleware.file_change import ContentLockMiddleware, FileChangeNotifyMiddleware
from app.agent.loop.middleware.hidden_context import HiddenContextInjectionMiddleware
from app.agent.loop.middleware.recap import RecapInjectionMiddleware
from app.agent.loop.middleware.steer import SteerInjectionMiddleware
from app.agent.loop.middleware.workspace_guard import WorkspaceGuardMiddleware
from app.agent.loop.middleware.write_approval import WriteApprovalMiddleware

__all__ = [
    "RecapInjectionMiddleware",
    "SteerInjectionMiddleware",
    "WorkspaceGuardMiddleware",
    "FileChangeNotifyMiddleware",
    "ContentLockMiddleware",
    "HiddenContextInjectionMiddleware",
    "WriteApprovalMiddleware",
]
