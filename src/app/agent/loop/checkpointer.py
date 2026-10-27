#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.checkpointer
文件名: checkpointer.py
作者: xionggaoqing
时间: 2026-10-27
说明: LangGraph checkpoint 持久化（手册「二、(4) 持久化双轨」第 2 轨）。

    `AsyncSqliteSaver` 落 `data/agent_state.db`（与业务库 `app.db` 分离，跨库不建外键）：
    供 Agent 恢复运行与 HITL 中断续跑；以 `thread_id = novel:{project_id}:session:{session_id}` 关联。

    进程内单例：aiosqlite 连接须长生命周期持有（每个 run 重开会丢中断态）。
    `DeepAgentState.messages` 由 deepagents 用 `DeltaChannel(snapshot_frequency=50)` 增量存储，
    checkpoint 体积 O(N) 而非 O(N²)。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_saver: Any = None
_conn: Any = None
_saver_path: Path | None = None
_lock = asyncio.Lock()


async def get_checkpointer() -> Any:
    """取（或惰性创建）`AsyncSqliteSaver`。数据根变化时自动重开。"""
    global _saver, _conn, _saver_path

    target = get_settings().agent_state_path
    if _saver is not None and _saver_path == target:
        return _saver

    async with _lock:
        # 双检：可能在等锁期间已被其它协程重建
        if _saver is not None and _saver_path == target:
            return _saver
        await _close_locked()

        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        target.parent.mkdir(parents=True, exist_ok=True)
        _conn = await aiosqlite.connect(str(target), check_same_thread=False)
        _saver = AsyncSqliteSaver(_conn)
        await _saver.setup()
        _saver_path = target
        logger.info("checkpointer_ready", path=str(target))
        return _saver


async def _close_locked() -> None:
    global _saver, _conn, _saver_path
    conn, _conn = _conn, None
    _saver, _saver_path = None, None
    if conn is not None:
        try:
            await conn.close()
        except Exception as exc:  # pragma: no cover
            logger.warning("checkpointer_close_failed", error=str(exc))


async def reset_checkpointer() -> None:
    """关闭连接（应用 shutdown / 测试隔离）。"""
    async with _lock:
        await _close_locked()
