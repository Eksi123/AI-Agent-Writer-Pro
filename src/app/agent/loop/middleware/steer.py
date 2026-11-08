# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.steer
文件名: steer.py
作者: xionggaoqing
时间: 2026-10-29
说明: `SteerInjectionMiddleware`：运行中「通过消息引导发送」。

    用户在大模型思考期间追加引导消息时，API 层先把引导落库（kind=steer，
    重进会话/重启服务仍可见），再入 `run_manager` 的 run 级队列；
    本中间件在**每次模型调用前**取出队列内容，作为 `HumanMessage` 追加到消息末尾，
    使正在跑的 run 立即受到引导（不中断、不新建 run），并发 `steer.consumed`
    事件通知前端「引导已被消费」（前端据此把「AI 思考」移到引导气泡下方）。

    队列项 = {"message_id", "content"}：message_id 非空为用户引导（已落库，
    MessageSyncer 按 id 跳过重复落库）；为空为系统内部纠正（熔断提醒等），
    注入时 name=steer_internal，不落树、不展示。

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
        """`steer_provider`：`(run_id) -> list[{"message_id","content"}]`，取出并清空队列。"""
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
            items = self._steer_provider(run_id) or []
        except Exception as exc:  # pragma: no cover - 引导取用失败不阻断生成
            logger.warning("steer_fetch_failed", error=str(exc))
            return None
        if not items:
            return None

        messages = list(state.get("messages") or [])
        consumed: list[str] = []
        added = 0
        for item in items:
            text = str(item.get("content") or "").strip()
            if not text:
                continue
            message_id = item.get("message_id")
            if message_id:
                # 用户引导：已落库（kind=steer）；带 id 供 MessageSyncer 去重
                messages.append(
                    HumanMessage(
                        content=text,
                        name="steer",
                        additional_kwargs={"steer_message_id": message_id},
                    )
                )
                consumed.append(message_id)
            else:
                # 系统内部纠正（熔断提醒等）：不落树、不展示
                messages.append(HumanMessage(content=text, name="steer_internal"))
            added += 1
        if added == 0:
            return None
        if consumed:
            emit = getattr(context, "emit", None)
            publish = getattr(emit, "publish", None)
            if publish is not None:
                try:
                    await publish("steer.consumed", {"message_ids": consumed})
                except Exception as exc:  # pragma: no cover - 事件失败不阻断生成
                    logger.warning("steer_consumed_emit_failed", run_id=run_id, error=str(exc))
        logger.info("steer_messages_injected", run_id=run_id, count=len(consumed))
        return {"messages": messages}
