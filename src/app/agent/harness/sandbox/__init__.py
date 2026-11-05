# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.sandbox
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 沙箱：CompositeBackend 路由 + 8 条有序 permissions（手册「二、(5)」）。

    自 `agent/sandbox/` 迁入 harness（安全措施归类）；新增 `virtual_path.py`
    （虚拟路径分类公共件，loop 的 FileChangeNotify 与 harness/lock 的 ContentLock 共用）。
"""

from app.agent.harness.sandbox.backend import build_backend
from app.agent.harness.sandbox.permissions import PERMISSION_RULES, build_permissions
from app.agent.harness.sandbox.virtual_path import classify_change

__all__ = ["PERMISSION_RULES", "build_backend", "build_permissions", "classify_change"]
