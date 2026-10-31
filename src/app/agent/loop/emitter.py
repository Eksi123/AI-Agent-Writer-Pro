# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.emitter
文件名: emitter.py
作者: xionggaoqing
时间: 2026-10-27
说明: 事件发射器：绑定 (run_id, session_id) 的 SSE 发布快捷方式。

    中间件与 runner 经 `AgentContext.emit` 使用，避免每处都传 run/session 三元组。
    同时承载 token 预算报告（RecapInjection 用）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.llm.budget import budget_report
from app.agent.loop.events import sse_bus


@dataclass(slots=True)
class Emitter:
    run_id: str
    session_id: str
    max_context: int = 32768
    reserve_for_output: int = 4096

    async def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        await sse_bus.publish(self.run_id, self.session_id, event, data or {})

    def schedule(self, event: str, data: dict[str, Any] | None = None) -> Any:
        return sse_bus.schedule(self.run_id, self.session_id, event, data or {})

    def budget_report(self, current_tokens: int) -> dict[str, Any]:

        return budget_report(
            current_tokens,
            max_context=self.max_context,
            reserve_for_output=self.reserve_for_output,
        )
