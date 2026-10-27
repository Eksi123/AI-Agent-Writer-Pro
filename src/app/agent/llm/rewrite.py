# -*- coding: utf-8 -*-
"""
模块: app.agent.llm.rewrite
文件名: rewrite.py
作者: xionggaoqing
时间: 2026-10-27
说明: 选区 AI 改写（手册「三、(3)【正文】」选中浮层：改写/扩写/缩写/润色）。

    服务层**直连 ChatModel**（同门禁 G1/G3，不进 Agent 循环——单次转换无需工具与多轮）。
    返回改写文本 + 原文回传，由前端做 diff 查看与「来源标记 + 回滚」。
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm.factory import get_chat_model, get_default_chat_model
from app.agent.prompts import render
from app.agent.tracing import observation
from app.core.errors import BadRequestError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: 允许的改写模式（与前端选中浮层四按钮一一对应）
REWRITE_MODES = ("rewrite", "expand", "condense", "polish")

#: 模式 → 中文标签（提示词与审计用）
REWRITE_MODE_LABELS = {
    "rewrite": "改写",
    "expand": "扩写",
    "condense": "缩写",
    "polish": "润色",
}

#: 单次选区上限（字符）：防止把整章塞进一次改写
_MAX_SELECTED_CHARS = 8000


#: 成对包裹符号（左 → 右）。中文引号是不对称对（“ ” / 「 」 / 『 』），不能用首尾相等判断。
_QUOTE_PAIRS = (
    ("“", "”"),
    ("「", "」"),
    ("『", "』"),
    ("‘", "’"),
    ("'", "'"),
    ('"', '"'),
)


def _strip_wrappers(text: str) -> str:
    """去掉模型可能包裹的引号/代码块/前后缀说明，只留改写正文。"""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
    t = re.sub(r"\n?```$", "", t)
    t = t.strip()
    # 模型有时回「改写后：」这类前缀
    t = re.sub(r"^(改写后|改写结果|结果|输出)[：:]\s*", "", t)
    # 成对包裹的引号（含中文不对称引号）
    for left, right in _QUOTE_PAIRS:
        if len(t) >= 2 and t.startswith(left) and t.endswith(right):
            t = t[len(left) : -len(right)].strip()
            break
    return t


def _message_text(resp: Any) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content)


async def rewrite_selection(
    session: AsyncSession,
    *,
    selected_text: str,
    mode: str,
    model_ref: str | None = None,
    chapter_title: str = "",
    context_before: str = "",
    context_after: str = "",
    instruction: str = "",
) -> dict[str, Any]:
    """对选中文字执行 AI 改写。

    Args:
        selected_text: 选中的正文（纯文本）
        mode: rewrite|expand|condense|polish
        model_ref: 单条消息级模型覆盖（空则用默认模型）
        chapter_title: 所在章标题（风格参考）
        context_before/after: 选区前后文（各 ≤600 字，帮助衔接）
        instruction: 用户补充指示（可空）

    Returns:
        {mode, original, rewritten, model_ref, word_counts, prompt_version}
    """
    selected_text = (selected_text or "").strip()
    if not selected_text:
        raise BadRequestError("选中文字不能为空")
    if len(selected_text) > _MAX_SELECTED_CHARS:
        raise BadRequestError(f"选中文字超过 {_MAX_SELECTED_CHARS} 字符上限，请缩小选区")
    if mode not in REWRITE_MODES:
        raise BadRequestError(f"mode 必须是 {REWRITE_MODES} 之一，收到：{mode}")

    if model_ref:
        chat_model = await get_chat_model(session, model_ref)
        resolved_ref = model_ref
    else:
        resolved_ref, chat_model = await get_default_chat_model(session)

    prompt = render(
        "rewrite_selection.md.j2",
        mode=mode,
        mode_label=REWRITE_MODE_LABELS[mode],
        selected_text=selected_text,
        chapter_title=chapter_title or "（未知章节）",
        context_before=(context_before or "")[-600:],
        context_after=(context_after or "")[:600],
        instruction=(instruction or "").strip(),
    )

    with observation(
        "rewrite_selection", metadata={"mode": mode, "chars": len(selected_text), "model_ref": resolved_ref}
    ):
        try:
            resp = await chat_model.ainvoke(
                [
                    SystemMessage(content="你是中文小说文字编辑。只输出处理后的正文本身，绝不输出解释、标题或引号包裹。"),
                    HumanMessage(content=prompt),
                ]
            )
        except Exception as exc:
            logger.warning("rewrite_llm_failed", mode=mode, error=str(exc))
            raise BadRequestError(f"改写失败：{type(exc).__name__}: {str(exc)[:300]}") from exc

    rewritten = _strip_wrappers(_message_text(resp))
    if not rewritten:
        raise BadRequestError("模型返回为空，请重试或更换模型")

    return {
        "mode": mode,
        "original": selected_text,
        "rewritten": rewritten,
        "model_ref": resolved_ref,
        "word_counts": {"original": len(selected_text), "rewritten": len(rewritten)},
        "prompt_version": "rewrite_selection:v1",
    }
