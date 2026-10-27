# -*- coding: utf-8 -*-
"""
模块: app.db.session
文件名: session.py
作者: xionggaoqing
时间: 2026-10-27
说明: async engine / sessionmaker（aiosqlite）。

    业务库 `data/app.db`；LangGraph checkpoint 另用 `data/agent_state.db`（M3），跨库不建外键。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.base import enable_sqlite_pragmas

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _database_url() -> str:
    path = get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows 路径需转成正斜杠 URL
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def get_engine() -> AsyncEngine:
    """进程内单例 engine。测试中用 `reset_engine()` 释放。"""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(
            _database_url(),
            echo=False,
            future=True,
            connect_args={"timeout": 30, "check_same_thread": False},
        )
        enable_sqlite_pragmas(_engine)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None  # noqa: S101 - get_engine 保证已初始化
    return _sessionmaker


async def reset_engine() -> None:
    """释放 engine（应用关闭 / 测试隔离时调用）。"""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """事务边界：正常提交，异常回滚。服务层用这个而非裸 session。"""
    session = get_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用（`Depends(get_db)`）。

    每个请求一个 session；提交由路由/服务层显式控制，异常时回滚。
    """
    session = get_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
