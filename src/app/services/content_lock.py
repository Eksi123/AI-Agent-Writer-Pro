# -*- coding: utf-8 -*-
"""
模块: app.services.content_lock
文件名: content_lock.py
作者: xionggaoqing
时间: 2026-11-01
说明: 章节正文「AI 编辑锁」（v1101）。

    AI（流程生成 / Agent 写正文）修改某章期间，用户不得同时修改该章：
    - `PUT /chapters/{id}/content` 在锁定期内一律 409 / 40903（手动与自动保存同拒）；
    - 前端经 `GET /chapters/locks` 轮询 + SSE `content.lock` 事件展示
      「AI编辑中，请稍后再试……」并禁用保存。

    进程内注册表（单用户本地应用，与 RunManager 同生命周期）；
    锁的获取/释放统一在 run 收尾（`release_for_run`）完成，含异常与取消路径。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class ContentLock:
    chapter_id: str
    run_id: str
    session_id: str
    project_id: str


class ContentLockRegistry:
    """chapter_id → 锁 的进程内注册表。"""

    def __init__(self) -> None:
        self._locks: dict[str, ContentLock] = {}
        self._mu = asyncio.Lock()

    async def acquire(self, chapter_id: str, *, run_id: str, session_id: str, project_id: str) -> bool:
        """加锁；同章已被**其它 run** 占用则失败（不覆盖，避免误释放他人锁）。"""
        if not chapter_id:
            return False
        async with self._mu:
            existing = self._locks.get(chapter_id)
            if existing is not None and existing.run_id != run_id:
                logger.warning(
                    "content_lock_busy", chapter_id=chapter_id, holder=existing.run_id, requester=run_id
                )
                return False
            self._locks[chapter_id] = ContentLock(
                chapter_id=chapter_id, run_id=run_id, session_id=session_id, project_id=project_id
            )
            return True

    async def release(self, chapter_id: str, *, run_id: str) -> bool:
        """释放锁；仅当持有者匹配（防止后到的 run 误删先到的锁）。"""
        if not chapter_id:
            return False
        async with self._mu:
            existing = self._locks.get(chapter_id)
            if existing is None or existing.run_id != run_id:
                return False
            del self._locks[chapter_id]
            return True

    async def release_for_run(self, run_id: str) -> list[str]:
        """释放某 run 持有的全部锁（run 收尾统一调用）。"""
        async with self._mu:
            freed = [cid for cid, lk in self._locks.items() if lk.run_id == run_id]
            for cid in freed:
                del self._locks[cid]
            return freed

    def is_locked(self, chapter_id: str) -> bool:
        return chapter_id in self._locks

    def lock_of(self, chapter_id: str) -> ContentLock | None:
        return self._locks.get(chapter_id)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "chapter_id": lk.chapter_id,
                "run_id": lk.run_id,
                "session_id": lk.session_id,
                "project_id": lk.project_id,
            }
            for lk in self._locks.values()
        ]


#: 进程内单例
content_locks = ContentLockRegistry()
