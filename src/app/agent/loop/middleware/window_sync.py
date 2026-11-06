# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.window_sync
文件名: window_sync.py
作者: xionggaoqing
时间: 2026-11-06
说明: `ContextWindowSyncMiddleware`：上下文窗口配置实时同步（压缩阈值动态跟随）。

    压缩中间件的触发阈值在**装配 graph 时**按当时的 Context Length 固定；若用户
    中途在「AI 设置」把窗口改小，正在跑的 run 仍按旧阈值判定——可能出现
    「比例已超 20% 剩余却不压缩」的窗口期。

    本中间件在**每次模型调用前**（且排在压缩中间件之前）实时读取模型行的
    `max_context`，重算触发点（窗口 × 80%，即剩余 <20% 压缩）并**原地改写**
    压缩中间件的阈值子句——窗口变小后下一次模型调用即按新阈值判定、该压缩就压缩。

    读取带 5 秒 TTL 缓存，避免每次模型调用都查库。
"""

from __future__ import annotations

import time
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 窗口配置读取缓存 TTL（秒）——改配置后最迟 5 秒生效
_WINDOW_CACHE_TTL = 5.0

#: 触发点 = 窗口 × 80%（剩余 <20% 压缩），与 builder 装配口径一致
_TRIGGER_FRACTION = 0.8


class ContextWindowSyncMiddleware(AgentMiddleware):
    """每次模型调用前把压缩阈值同步到最新 Context Length 配置。"""

    name = "ContextWindowSync"

    def __init__(self, summarization_mw: Any, window_provider: Any = None) -> None:
        """`summarization_mw`：压缩中间件实例（deepagents 或 langchain 版）。

        `window_provider`：`async (model_ref) -> int | None`，返回最新配置窗口；
        缺省用内置 DB 查询实现。
        """
        super().__init__()
        self._summarization = summarization_mw
        self._window_provider = window_provider or _default_window_of
        self._cache: dict[str, tuple[int | None, float]] = {}

    async def _window_of(self, model_ref: str) -> int | None:
        now = time.monotonic()
        hit = self._cache.get(model_ref)
        if hit is not None and now - hit[1] < _WINDOW_CACHE_TTL:
            return hit[0]
        try:
            window = await self._window_provider(model_ref)
        except Exception as exc:  # pragma: no cover - 读配置失败不阻断生成
            logger.warning("context_window_read_failed", model_ref=model_ref, error=str(exc))
            return hit[0] if hit else None
        self._cache[model_ref] = (window, now)
        return window

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        context = getattr(runtime, "context", None)
        model_ref = str(getattr(context, "model_ref", "") or "")
        if not model_ref:
            return None
        window = await self._window_of(model_ref)
        if not window:
            return None

        helper = getattr(self._summarization, "_lc_helper", self._summarization)
        clauses = getattr(helper, "_trigger_clauses", None)
        if not clauses:
            return None
        want = max(2000, int(window * _TRIGGER_FRACTION))
        current: int | None = None
        for clause in clauses:
            if isinstance(clause, dict) and "tokens" in clause:
                current = int(clause["tokens"])
                break
        if current == want:
            return None
        for clause in clauses:
            if isinstance(clause, dict) and "tokens" in clause:
                clause["tokens"] = want
        logger.info(
            "context_window_synced", model_ref=model_ref, window=window, trigger=want, previous=current
        )
        return None


async def _default_window_of(model_ref: str) -> int | None:
    """按 model_ref 查模型行最新 `max_context`（Context Length 配置）。"""
    from app.db.session import session_scope
    from app.services import llm as llm_service

    async with session_scope() as db:
        refs = await llm_service.all_model_refs(db)
    for row in refs or []:
        if row.get("model_ref") == model_ref:
            value = int(row.get("max_context") or 0)
            return value or None
    return None


__all__ = ["ContextWindowSyncMiddleware"]
