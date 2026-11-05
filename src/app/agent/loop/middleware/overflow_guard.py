# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.middleware.overflow_guard
文件名: overflow_guard.py
作者: xionggaoqing
时间: 2026-11-04
说明: `ContextOverflowGuardMiddleware`——「设定 Context Length ≠ 模型真实窗口」的兜底。

    弹窗里的 Context Length 是用户声明值，可能大于模型真实窗口。此时压缩中间件
    （按声明窗口 90% 触发）不会介入，provider 会直接报上下文超限错误。
    本中间件挂在压缩中间件**外层**：捕获 provider 的超限错误（含 DashScope 的
    「Range of input length should be [...]」等 deepagents 未识别的文案），
    把旧消息摘要成一条 system 消息 + 保留最近若干条，再重试一次。
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.core.logging import get_logger

logger = get_logger(__name__)

#: provider 超限错误文案特征（小写匹配；覆盖 OpenAI / Anthropic / DashScope / Gemini 常见措辞）
_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "contextwindowexceedederror",
    "maximum context length",
    "exceeds the context window",
    "exceeds the available context size",
    "context window exceeded",
    "context limit exceeded",
    "input tokens exceed the configured limit",
    "prompt is too long",
    # DashScope / 通义：Range of input length should be [1, 997952]
    "range of input length",
    "input length should be",
    "too many tokens",
    "reduce the length",
    "token limit",
)

_SUMMARY_PROMPT = (
    "你是上下文压缩助手。下面是一段被截断展示的对话历史，请提取继续任务所需的最关键信息，"
    "输出简洁的中文要点（会话目标 / 已完成结论 / 涉及文件与改动 / 下一步），"
    "只输出要点本身，不要多余解释。"
)

#: 压缩后保留的最近消息条数
_KEEP_MESSAGES = 6
#: 送进摘要模型的旧历史渲染上限（字符），避免摘要调用自身超限
_RENDER_CAP = 120_000


def looks_like_context_overflow(exc: BaseException) -> bool:
    """识别 provider 的上下文超限错误（状态码 400/413/422 + 文案特征）。"""
    status = getattr(exc, "status_code", None)
    if status not in (400, 413, 422):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in _OVERFLOW_MARKERS)


class ContextOverflowGuardMiddleware(AgentMiddleware):
    """捕获上下文超限错误 → 主动摘要压缩历史 → 重试一次。"""

    name = "ContextOverflowGuard"

    def __init__(self, model: Any, *, keep_messages: int = _KEEP_MESSAGES) -> None:
        super().__init__()
        self._model = model
        self._keep = max(2, int(keep_messages))

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> ModelResponse:
        try:
            return await handler(request)
        except Exception as exc:
            if not looks_like_context_overflow(exc):
                raise
            compacted = await self._compact(request)
            if compacted is None:
                raise
            logger.warning(
                "context_overflow_compacted",
                error=str(exc)[:300],
                before=len(request.messages),
                after=len(compacted),
            )
            return await handler(request.override(messages=compacted))

    async def _compact(self, request: ModelRequest) -> list[BaseMessage] | None:
        """旧消息 → 一条摘要 system 消息；保留最近 `keep` 条原文。无法再压缩时返回 None。"""
        messages = list(request.messages)
        if len(messages) <= self._keep + 1:
            return None
        old, recent = messages[: -self._keep], messages[-self._keep :]

        rendered: list[str] = []
        total = 0
        for m in old:
            line = f"{getattr(m, 'type', 'message')}: {_text_of(m)}"
            if total + len(line) > _RENDER_CAP:
                rendered.append("…（更早历史已省略）")
                break
            rendered.append(line)
            total += len(line)
        if not rendered:
            return None

        try:
            reply = await self._model.ainvoke(
                [SystemMessage(content=_SUMMARY_PROMPT), HumanMessage(content="\n".join(rendered))]
            )
            summary = _text_of(reply).strip()
        except Exception as exc:  # 摘要调用自身失败 → 放弃压缩，原错误上抛
            logger.warning("context_overflow_summarize_failed", error=str(exc)[:300])
            return None
        if not summary:
            return None

        return [SystemMessage(content=f"【上下文压缩摘要】\n{summary}", name="overflow_summary"), *recent]


def _text_of(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content[:4000]
    if isinstance(content, list):  # 多模态块：只取文本部分
        parts = [str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(parts)[:4000]
    return str(content)[:4000]
