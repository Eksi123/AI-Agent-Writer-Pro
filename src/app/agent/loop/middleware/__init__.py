# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 自定义中间件（手册「二、(2)」表，挂载点用真实 API）。

装配顺序：WriteApproval → ModelRetry → ModelSwitchOnFailure（连续失败才切换）
→ ModelCallLimit → TodoList → RecapInjection → HiddenContext → PairingRepair
→ WastefulLoopGuard → SteerInjection → ReadScopeGuard → WorkspaceGuard
→ FileChangeNotify → ContentLock。

    （架构优化）：安全类中间件已迁至 harness——
    `WriteApprovalMiddleware`（harness/approval）、`WorkspaceGuardMiddleware`（harness/guard）、
    `ContentLockMiddleware`（harness/lock）；本包保留**功能性**中间件
    （recap 注入 / steer 引导 / hidden_context / 文件变更通知），并统一 re-export
    装配清单，供 loop/builder 按顺序取用（import 路径对外保持稳定）。
"""

from app.agent.harness.approval.write_approval import WriteApprovalMiddleware
from app.agent.harness.guard.read_scope import ReadScopeGuardMiddleware
from app.agent.harness.guard.workspace_guard import WorkspaceGuardMiddleware
from app.agent.harness.lock.content_lock import ContentLockMiddleware
from app.agent.loop.middleware.file_change import FileChangeNotifyMiddleware
from app.agent.loop.middleware.hidden_context import HiddenContextInjectionMiddleware
from app.agent.loop.middleware.overflow_guard import ContextOverflowGuardMiddleware
from app.agent.loop.middleware.pairing_repair import PairingRepairMiddleware
from app.agent.loop.middleware.recap import RecapInjectionMiddleware
from app.agent.loop.middleware.steer import SteerInjectionMiddleware
from app.agent.loop.middleware.window_sync import ContextWindowSyncMiddleware

__all__ = [
    "RecapInjectionMiddleware",
    "SteerInjectionMiddleware",
    "WorkspaceGuardMiddleware",
    "FileChangeNotifyMiddleware",
    "ContentLockMiddleware",
    "HiddenContextInjectionMiddleware",
    "WriteApprovalMiddleware",
    "ContextOverflowGuardMiddleware",
    "ReadScopeGuardMiddleware",
    "PairingRepairMiddleware",
    "ContextWindowSyncMiddleware",
]
