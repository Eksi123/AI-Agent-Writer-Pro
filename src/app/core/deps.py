# -*- coding: utf-8 -*-
"""
模块: app.core.deps
文件名: deps.py
作者: xionggaoqing
时间: 2026-10-27
说明: 依赖注入：DB session、服务实例（手册「三、(1) core/deps.py」）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.workspace import WorkspaceGuard


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个 session（异常回滚，正常提交）。"""
    async for session in get_db():
        yield session


def get_guard(project_id: str) -> WorkspaceGuard:
    """按项目取 WorkspaceGuard（服务层唯一文件写入口）。"""
    return WorkspaceGuard(project_id)


class GuardFactory:
    """便于 `Depends` 注入的守卫工厂。"""

    def __call__(self, project_id: str) -> WorkspaceGuard:
        return WorkspaceGuard(project_id)


guard_factory = GuardFactory()
SessionDep = Depends(get_session)
