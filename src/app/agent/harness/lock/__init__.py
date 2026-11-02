# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.lock
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: AI 编辑锁（★v1101）：Agent 写入某章正文时给该章加锁，锁定期内用户保存
    （含自动保存）被 `PUT /chapters/{id}/content` 拒绝（409/40903）；
    锁在 run 收尾统一释放并广播 `content.lock` SSE 事件。

    锁本体（`content_locks` 单例）在 `services/content_lock.py`（应用层状态），
    这里只放 Agent 侧的加锁中间件（运行期安全措施）。
"""

from app.agent.harness.lock.content_lock import ContentLockMiddleware

__all__ = ["ContentLockMiddleware"]
