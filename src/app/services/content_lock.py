# -*- coding: utf-8 -*-
"""
模块: app.services.content_lock
文件名: content_lock.py
作者: xionggaoqing
时间: 2026-11-01
说明: 章节正文「AI 编辑锁」。

    AI（流程生成 / Agent 写正文）修改某章期间，用户不得同时修改该章：
    - `PUT /chapters/{id}/content` 在锁定期内一律 409 / 40903（手动与自动保存同拒）；
    - 前端靠 SSE `content.lock` 事件展示「AI编辑中，请稍后再试……」并禁用保存
      （**无轮询**：`GET /chapters/locks` 仅用于页面刷新后补状态，事件名见
      `web/src/services/sse.ts` 的白名单）。

    进程内注册表（单用户本地应用，与 RunManager 同生命周期）；
    锁的获取在**写入之前**（见 `harness/lock/content_lock.py`），释放统一在 run
    收尾（`release_for_run`）完成，含异常与取消路径。

    ★ TTL 兜底：run 暂停等待审批时 finalize **不会**执行（设计如此，见
    `loop/runner.py` 的暂停路径），若用户把审批卡片晾着不管，锁就永久占住该章，
    之后任何保存都是 40903 且无从解释。故超过 `LOCK_TTL_SEC` 的锁一律视为失效：
    读取时忽略、`acquire` 时可被抢占。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 锁的最长持有时间（秒）。取值需远大于「生成一章」的正常耗时，
#: 又要短到不至于让用户被晾一整天——2 小时是折中。
LOCK_TTL_SEC = 2 * 60 * 60


@dataclass(slots=True)
class ContentLock:
    chapter_id: str
    run_id: str
    session_id: str
    project_id: str
    #: `time.monotonic()` 基准，避免系统时钟回拨导致 TTL 判定漂移
    acquired_at: float = field(default_factory=time.monotonic)


class ContentLockRegistry:
    """chapter_id → 锁 的进程内注册表。"""

    def __init__(self) -> None:
        self._locks: dict[str, ContentLock] = {}
        self._mu = asyncio.Lock()

    @staticmethod
    def _expired(lock: ContentLock) -> bool:
        """锁是否已超过 TTL（失效锁应被忽略 / 可被抢占）。"""
        return (time.monotonic() - lock.acquired_at) > LOCK_TTL_SEC

    async def acquire(self, chapter_id: str, *, run_id: str, session_id: str, project_id: str) -> bool:
        """加锁；同章已被**其它 run** 占用则失败（不覆盖，避免误释放他人锁）。

        已过 TTL 的锁视为失效：直接抢占并留痕（正常情况下 run 收尾就该释放它，
        没释放说明该 run 卡在审批/异常路径上，此时不该继续把章节锁死）。
        """
        if not chapter_id:
            return False
        async with self._mu:
            existing = self._locks.get(chapter_id)
            if existing is not None and self._expired(existing):
                logger.warning(
                    "content_lock_expired_stolen",
                    chapter_id=chapter_id,
                    stale_holder=existing.run_id,
                    ttl_sec=LOCK_TTL_SEC,
                )
                existing = None
                self._locks.pop(chapter_id, None)
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

    async def release_for_run(self, run_id: str) -> list[tuple[str, str]]:
        """释放某 run 持有的全部锁（run 收尾统一调用）。

        Returns:
            `[(资源键, project_id), ...]`。**必须带 project_id**：前端收到
            `content.lock(locked=False)` 后要靠它把项目从 `lockedProjects` 集合里
            摘掉，缺了这个字段项目会一直显示「AI 编辑中」直到刷新页面。

            顺带清掉已过 TTL 的残留锁（那些锁的 run 早已不在，没人会再释放它们）。
        """
        async with self._mu:
            freed: list[tuple[str, str]] = [
                (cid, lk.project_id)
                for cid, lk in self._locks.items()
                if lk.run_id == run_id or self._expired(lk)
            ]
            for cid, _pid in freed:
                del self._locks[cid]
            return freed

    def _live(self) -> dict[str, ContentLock]:
        """未过期的锁（同步读取路径共用；单线程事件循环内读字典是安全的）。"""
        return {cid: lk for cid, lk in self._locks.items() if not self._expired(lk)}

    def is_locked(self, chapter_id: str) -> bool:
        lock = self._locks.get(chapter_id)
        return lock is not None and not self._expired(lock)

    def lock_of(self, chapter_id: str) -> ContentLock | None:
        lock = self._locks.get(chapter_id)
        if lock is None or self._expired(lock):
            return None
        return lock

    def keys_for_project(self, project_id: str) -> list[str]:
        """某项目下全部被锁资源键（章 id / `outline:{pid}`），供删除类操作拦截。"""
        return [cid for cid, lk in self._live().items() if lk.project_id == project_id]

    def is_project_busy(self, project_id: str) -> bool:
        return bool(self.keys_for_project(project_id))

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "chapter_id": lk.chapter_id,
                "run_id": lk.run_id,
                "session_id": lk.session_id,
                "project_id": lk.project_id,
                #: 已持锁秒数，便于排查「锁一直没释放」类问题
                "held_sec": round(time.monotonic() - lk.acquired_at, 1),
            }
            for lk in self._live().values()
        ]


#: 进程内单例
content_locks = ContentLockRegistry()
