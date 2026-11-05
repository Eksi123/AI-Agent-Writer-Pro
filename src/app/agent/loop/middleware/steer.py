# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.steer
文件名: steer.py
作者: xionggaoqing
时间: 2026-10-29
说明: `SteerInjectionMiddleware`：运行中「通过消息引导发送」。

    用户在大模型思考期间追加引导消息时，消息先入 `run_manager` 的 run 级队列，
    本中间件在**每次模型调用前**取出队列内容，作为 `HumanMessage` 追加到消息末尾，
    使正在跑的 run 立即受到引导（不中断、不新建 run）。

    与 `RecapInjectionMiddleware` 的区别：recap 注入的是 system 前情提要（插在最前），
    本中间件注入的是用户即时引导（追加在最后，最贴近模型的下一次决策）。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import HumanMessage

from app.core.logging import get_logger

logger = get_logger(__name__)


class SteerInjectionMiddleware(AgentMiddleware):
    """把运行中收到的用户引导消息注入到下一次模型调用。"""

    name = "SteerInjection"

    def __init__(self, steer_provider: Any = None) -> None:
        """`steer_provider`：`(run_id) -> list[str]`，取出并清空该 run 的引导队列。"""
        super().__init__()
        self._steer_provider = steer_provider

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if self._steer_provider is None:
            return None
        context = getattr(runtime, "context", None)
        run_id = getattr(context, "run_id", None)
        if not run_id:
            return None

        try:
            texts = self._steer_provider(run_id) or []
        except Exception as exc:  # pragma: no cover - 引导取用失败不阻断生成
            logger.warning("steer_fetch_failed", error=str(exc))
            return None
        if not texts:
            return None


        messages = list(state.get("messages") or [])
        for text in texts:
            text = (text or "").strip()
            if text:
                messages.append(HumanMessage(content=text, name="steer"))
        logger.info("steer_messages_injected", run_id=run_id, count=len(texts))
        return {"messages": messages}
