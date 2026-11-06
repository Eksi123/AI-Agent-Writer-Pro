# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.context_guard
文件名: context_guard.py
作者: xionggaoqing
时间: 2026-11-06
说明: `ContextGuardMiddleware`：上下文获取的**两层保险第二层**。

    第一层 = 确定性 provider（recap / outline_ctx 三级定位链），命中即注入；
    第二层 = 本中间件：provider 全未命中时，检查用户消息是否含**明确**的
    「回顾已生成章节」或「基于大纲XX内容生成」指令：
    - 有明确指令 → 注入一条 system 提示，要求模型**自行**用 outline_grep /
      recaller / read_file 获取所需上下文后再推进（获取失败要如实说明）；
    - 无明确指令 → **什么都不注入**——禁止模型自作主张抓取/编造内容。

    注入为请求级（`request.override`），不落 state、不入消息树。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

_MARK = "context_guard"

_GUARD_REVIEW = (
    "## 上下文获取提醒（系统）\n"
    "用户消息明确要求回顾/读取已生成的章节内容，但系统未能自动注入前情提要。\n"
    "请先自行获取所需上下文再推进：优先派发 recaller 子代理提取前情/人物/伏笔，"
    "或用 read_file 按 offset/limit 读取相关章节摘要（summaries/）；"
    "获取失败时在回复中如实说明缺什么、需要用户补充什么，**不得凭猜测编造前情**。"
)

_GUARD_OUTLINE_GEN = (
    "## 上下文获取提醒（系统）\n"
    "用户消息明确要求以大纲/附录/卷章内容为依据生成或修改小说，但系统未能自动注入对应摘录。\n"
    "请先自行获取依据再动笔：用 outline_grep 按卷名/章名/关键词摘录目标小节"
    "（简介+衔接要点），需要设定时摘录附录；确需整篇大纲或正文时 read_file"
    "（可能弹读取确认卡，被拒就改用 outline_grep 或询问用户）。\n"
    "**没有拿到依据就不要生成正文**——不得凭猜测编造剧情要点；"
    "若用户消息其实没有这类要求，忽略本提醒、按消息字面意图处理。"
)


class ContextGuardMiddleware(AgentMiddleware):
    """provider 未命中时的第二层保险：按用户明确指令提示模型自行获取上下文。"""

    name = "ContextGuard"

    def __init__(self, injected_slots_provider: Any = None) -> None:
        """`injected_slots_provider`：`(run_id) -> set[str]`，第一层已注入的槽位名。"""
        super().__init__()
        self._slots_provider = injected_slots_provider

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        context = getattr(request.runtime, "context", None)
        run_id = str(getattr(context, "run_id", "") or "")
        if not run_id:
            return await handler(request)

        # 第一层已注入任何上下文 → 不需要第二层
        if self._slots_provider is not None:
            try:
                if self._slots_provider(run_id):
                    return await handler(request)
            except Exception as exc:  # pragma: no cover
                logger.warning("context_guard_slots_failed", error=str(exc))

        from app.agent.loop.runner import run_manager
        from app.agent.workflows.writing_steps.intent import context_demand_kind

        try:
            kind = context_demand_kind(run_manager.user_text_of(run_id))
        except Exception as exc:  # pragma: no cover
            logger.warning("context_guard_demand_failed", error=str(exc))
            return await handler(request)
        if not kind:
            # 无明确需求：不注入任何提醒，禁止自作主张
            return await handler(request)

        text = _GUARD_REVIEW if kind == "review" else _GUARD_OUTLINE_GEN
        messages = [m for m in list(request.messages) if getattr(m, "name", None) != _MARK]
        from app.agent.loop.middleware.recap import append_injections

        messages = append_injections(messages, [SystemMessage(content=text, name=_MARK)])
        logger.info("context_guard_injected", run_id=run_id, kind=kind)
        return await handler(request.override(messages=messages))
