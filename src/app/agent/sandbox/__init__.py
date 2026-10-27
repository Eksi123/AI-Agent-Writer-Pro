# -*- coding: utf-8 -*-
"""
模块: app.agent.sandbox
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 沙箱：CompositeBackend 路由 + 8 条有序 permissions（手册「二、(5)」）。
"""

from app.agent.sandbox.backend import build_backend
from app.agent.sandbox.permissions import PERMISSION_RULES, build_permissions

__all__ = ["PERMISSION_RULES", "build_backend", "build_permissions"]
