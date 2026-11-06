# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.recap
文件名: recap.py
作者: xionggaoqing
时间: 2026-10-27
说明: `RecapInjectionMiddleware`：`awrap_model_call` 请求级注入上下文段 + token 预算检查。

    手册「二、(2)」：注入前情提要、token 预算检查（超阈值先触发裁剪）；
    敏感内容检查**仅预留挂载位，MVP 不实现**。

    注入走 `request.override(messages=...)`（只影响本次模型调用），
    不再回写 graph state——避免 `add_messages` 重放导致历史消息逐轮重复。

    两个独立槽位（各自 provider 返回空串即不注入）：
    - `recap`：前情提要（按需，见 `resolve.make_recap_provider` 门控）；
    - `outline_ctx`：目标章大纲摘录（写作意图命中时注入，代替整篇读大纲）。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

#: run 级「第一层已注入槽位」登记（ContextGuard 第二层保险据此判断是否需要兜底）。
#: key = run_id，value = 已注入非空内容的槽位名集合；run 结束由 runner 清理。
INJECTED_SLOTS: dict[str, set[str]] = {}


def injected_slots_of(run_id: str) -> set[str]:
    return INJECTED_SLOTS.get(run_id, set())


def clear_injected_slots(run_id: str) -> None:
    INJECTED_SLOTS.pop(run_id, None)


def append_injections(messages: list[Any], injected: list[Any]) -> list[Any]:
    """把注入的 system 消息追加到消息列表**尾部**（历史与当前用户消息之后）。

    token 缓存友好（对齐 Claude Code / Codex / Cursor 的做法：稳定内容靠前、
    易变内容靠尾）：provider 隐式前缀缓存按「从头开始的最长公共前缀」命中。
    - 同 run 内：注入段按 run 缓存、字节稳定且位置固定在尾部 → 每次调用
      `system + 历史 + 注入` 整体互为前缀，全命中；
    - 跨 run（同会话新消息）：注入段内容会变，但它在**尾部**，不影响
      `system + 全部历史` 这段公共前缀命中——若放在头部（system 之后），
      新 run 的注入段会在第 2 位切断前缀，整段历史全部 miss。
    多个注入段按中间件装配顺序依次追加（recap/outline_ctx → hidden → guard），
    顺序确定；同名旧注入先剔除再追加，保证每段只出现一次。
    """
    return messages + injected


class RecapInjectionMiddleware(AgentMiddleware):
    """在每次模型调用前，把前情提要 / 大纲摘录追加到 system 提示。

    注入改为**请求级**（`awrap_model_call` 里 `request.override(messages=...)`），
    不再通过 `abefore_model` 返回 state 更新——后者在 LangGraph `add_messages` 语义下
    会把整份 messages 列表当作「新增」重放，导致历史消息在 graph state 里逐轮重复，
    进而被 MessageSyncer 落库、前端渲染成「重复的问答」。请求级注入只影响本次模型调用，
    不写 state、不落消息树。
    """
    name = "RecapInjection"

    def __init__(
        self,
        recap_provider: Any = None,
        outline_ctx_provider: Any = None,
    ) -> None:
        """`*_provider`：`async (context) -> str`，返回空串表示本槽位不注入。"""
        super().__init__()
        self._recap_provider = recap_provider
        self._outline_ctx_provider = outline_ctx_provider
        self._cache: dict[str, str] = {}

    async def awrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        context = getattr(request.runtime, "context", None)
        recap = await self._build_slot(context, "recap", self._recap_provider)
        outline_ctx = await self._build_slot(context, "outline_ctx", self._outline_ctx_provider)

        # token 预算检查：超阈值记录告警（M4 接入实际裁剪）
        if context is not None:
            emit = getattr(context, "emit", None)
            if emit is not None:
                try:
                    from app.agent.harness.llm.budget import count_messages_tokens

                    tokens = count_messages_tokens(list(request.messages))
                    report = emit.budget_report(tokens)
                    if report.get("over_budget"):
                        await emit.publish(
                            "context.compact",
                            {
                                "before_tokens": tokens,
                                "after_tokens": tokens,
                                "reason": "over_budget_no_trim_in_m3",
                            },
                        )
                except Exception as exc:  # pragma: no cover
                    logger.debug("budget_check_failed", error=str(exc))

        segments = [(name, text) for name, text in (("recap", recap), ("outline_ctx", outline_ctx)) if text]
        # 登记本 run 已注入的槽位（ContextGuard 第二层保险据此判断是否需要兜底提醒）
        run_id = str(getattr(context, "run_id", "") or "") if context is not None else ""
        if run_id and segments:
            INJECTED_SLOTS.setdefault(run_id, set()).update(name for name, _ in segments)
        if not segments:
            return await handler(request)

        # 敏感内容检查挂载位（MVP 不实现，见手册「二、(2)」）
        self._check_sensitive(request)

        # 注入置于消息列表**头部**（system 之后、历史之前）：注入内容按 run 缓存、
        # 字节稳定，历史只在尾部追加 → 每次调用的公共前缀（system+注入+旧历史）
        # 最大化命中 provider 的隐式前缀缓存。
        messages = list(request.messages)
        injected = [SystemMessage(content=text, name=name) for name, text in segments]
        names = {name for name, _ in segments}
        messages = [m for m in messages if getattr(m, "name", None) not in names]
        messages = append_injections(messages, injected)
        # 请求级注入：只改本次调用的 messages，不回写 graph state
        return await handler(request.override(messages=messages))

    async def _build_slot(self, context: Any, slot: str, provider: Any) -> str:
        """构建单个注入槽位（同一 run 内结果不变 → 缓存，避免每次模型调用重复读文件）。"""
        if provider is None:
            return ""
        run_id = getattr(context, "run_id", "") or ""
        cache_key = f"{slot}:{run_id}"
        if run_id and cache_key in self._cache:
            return self._cache[cache_key]
        try:
            text = await provider(context) or ""
        except Exception as exc:  # 注入内容构建失败不阻断生成
            logger.warning("context_slot_build_failed", slot=slot, error=str(exc))
            return ""
        if run_id:
            self._cache[cache_key] = text
        return text

    @staticmethod
    def _check_sensitive(_request: Any) -> None:
        """敏感内容检查（预留挂载位，MVP 不实现）。"""
        return None
