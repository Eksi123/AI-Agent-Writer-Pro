# -*- coding: utf-8 -*-
"""
模块: app.agent.llm.budget
文件名: budget.py
作者: xionggaoqing
时间: 2026-10-27
说明: token 预算：近似计数（无需联网/无 Key），供 RecapInjection 裁剪判断。

    精确计数用已安装的 `tiktoken`（openai 系模型）；其它厂商回落近似估算
    （手册「八、①」标注 tiktoken 可用于精确 token 计数）。失败一律回落近似值，不抛异常。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 近似估算：中文按字符、西文按 ~4 字符/token 折算
_CHARS_PER_TOKEN = 3.0

_encoder_cache: dict[str, Any] = {}


def count_tokens_approx(text: str) -> int:
    """近似 token 数（中英混排安全下界估算）。"""
    if not text:
        return 0
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def count_tokens(text: str, *, model: str | None = None) -> int:
    """优先 tiktoken 精确计数，失败回落近似值。"""
    if not text:
        return 0
    if model:
        encoder = _get_encoder(model)
        if encoder is not None:
            try:
                return len(encoder.encode(text))
            except Exception:  # pragma: no cover - 特殊 token 等边缘情况
                pass
    return count_tokens_approx(text)


def _get_encoder(model: str) -> Any | None:
    if model in _encoder_cache:
        return _encoder_cache[model]
    try:
        import tiktoken

        enc = tiktoken.encoding_for_model(model)
    except Exception:
        enc = None
    _encoder_cache[model] = enc
    return enc


def count_messages_tokens(messages: list[Any], *, model: str | None = None) -> int:
    """消息列表的总 token 数（按 content 文本 + 工具调用参数估算）。"""
    import json

    total = 0
    for msg in messages:
        content = getattr(msg, "content", msg)
        if isinstance(content, str):
            total += count_tokens(content, model=model)
        else:
            total += count_tokens(json.dumps(content, ensure_ascii=False, default=str), model=model)
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            total += count_tokens(json.dumps(tool_calls, ensure_ascii=False, default=str), model=model)
    return total


def budget_report(
    current_tokens: int,
    *,
    max_context: int,
    reserve_for_output: int = 4096,
) -> dict[str, Any]:
    """预算报告：是否超阈值（触发裁剪）、剩余可用量。"""
    usable = max(0, max_context - reserve_for_output)
    over = current_tokens > usable
    return {
        "current_tokens": current_tokens,
        "max_context": max_context,
        "usable_tokens": usable,
        "remaining": max(0, usable - current_tokens),
        "over_budget": over,
        "usage_ratio": round(current_tokens / usable, 4) if usable else 1.0,
    }
