# -*- coding: utf-8 -*-
"""
模块: app.agent.tools.builtin.ask_user
文件名: ask_user.py
作者: xionggaoqing
时间: 2026-11-06
说明: Agent 内置工具 `ask_user`：以**多问题卡片**向用户提问并等待全部回答。

    AI 思考处理的中间过程需要用户决策（范围确认 / 方案选择 / 补充信息）时调用：
    - 落 `agent_interrupts(type=consult, kind=ask_user)` 行 + 发 SSE `consult.request`
      （payload 带 `questions` 清单），run 置 `awaiting_consult` 并经 langgraph
      `interrupt()` 暂停——与 call_budget / 流程咨询同一套 consult 契约；
    - 前端渲染多问题卡片（单选/多选选项 + 自定义答案输入、翻页、末题右下角「提交」），
      用户答完全部问题提交后走 `POST /agent/interrupts/{id}/resolve`
      （`decision=option`，`choice` = 答案清单 JSON）→ `runner.resume` 续跑；
    - resume 重放时按 `tool_call_id` 命中**已决**行：不建行、不发事件、不改状态，
      `interrupt()` 直接返回决定，工具把答案整理成 JSON 回给模型继续思考。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from langgraph.types import interrupt

from app.core.logging import get_logger
from app.db.session import session_scope

logger = get_logger(__name__)

#: 单次提问的问题数上限（卡片翻页承载能力）
MAX_QUESTIONS = 8


@tool("ask_user", parse_docstring=True)
async def ask_user(runtime: ToolRuntime, questions: list[dict[str, Any]]) -> str:
    """以卡片形式向用户提问并等待回答（需要用户决策来决定下一步思考方向时使用）。

    这是 AI 思考处理的中间过程：调用后会话暂停并弹出多问题卡片（选项单选/多选 +
    自定义答案输入框，可翻页），用户答完所有问题点「提交」后，本工具返回全部答案，
    你再据此继续思考处理。**不要在纯文本回复里罗列问题干等用户**——需要决策就调用本工具。

    ★问题与选项文案必须是人话：不要出现内部路径/文件名（chapters/v2/c1.md、
    volumes.yaml 等）或技术词，用「第二卷的三个章节」「卷目录」这类读者能懂的说法。

    Args:
        questions: 问题清单（1-8 个），每项形如
            `{"question": "问题文本", "header": "短标签(可选)",
              "options": [{"label": "选项文本", "description": "选项说明(可选)"}],
              "multi_select": false}`；
            `options` 为空表示自由文本题（用户直接在输入框作答）。

    Returns:
        JSON 字符串：`{"answers": [{"question", "header", "selected": [选项label...],
        "text": "自定义答案"}]}`，与 questions 顺序一一对应。
    """
    context = getattr(runtime, "context", None)
    run_id = str(getattr(context, "run_id", "") or "")
    session_id = str(getattr(context, "session_id", "") or "")
    emit = getattr(context, "emit", None)

    normalized = _normalize_questions(questions)
    if not normalized:
        return json.dumps({"error": "questions 不能为空"}, ensure_ascii=False)

    tool_call_id = str(getattr(runtime, "tool_call_id", "") or "")
    payload = {
        "kind": "ask_user",
        "consult_kind": "ask_user",
        "tool_call_id": tool_call_id,
        "questions": normalized,
    }

    interrupt_id, already_decided = await _find_or_create_row(
        run_id, session_id, tool_call_id, payload
    )
    if not already_decided:
        if emit is not None:
            await emit.publish(
                "consult.request",
                {
                    "interrupt_id": interrupt_id,
                    "kind": "ask_user",
                    "question": normalized[0]["question"],
                    "questions": normalized,
                    "options": [],
                    "allow_text": True,
                    "workflow_stage": getattr(context, "workflow_stage", "") or None,
                },
            )
        await _set_status(run_id, session_id, "awaiting_consult")

    decision = interrupt(payload)
    await _record_decision(interrupt_id, decision)
    answers = _answers_of(decision, normalized)
    logger.info(
        "ask_user_resolved", run_id=run_id, interrupt_id=interrupt_id, questions=len(normalized)
    )
    return json.dumps({"answers": answers}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 归一化 / 落库
# ---------------------------------------------------------------------------


def _normalize_questions(questions: Any) -> list[dict[str, Any]]:
    """入参容错归一化：字符串题 / 字符串选项 / 缺字段都补齐成卡片契约结构。"""
    if not isinstance(questions, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in questions[:MAX_QUESTIONS]:
        if isinstance(raw, str):
            raw = {"question": raw}
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("question") or "").strip()
        if not text:
            continue
        options: list[dict[str, str]] = []
        for opt in raw.get("options") or []:
            if isinstance(opt, str):
                label, desc = opt.strip(), ""
            elif isinstance(opt, dict):
                label = str(opt.get("label") or "").strip()
                desc = str(opt.get("description") or "").strip()
            else:
                continue
            if label:
                options.append({"label": label, "description": desc})
        out.append(
            {
                "question": text,
                "header": str(raw.get("header") or "").strip(),
                "options": options,
                "multi_select": bool(raw.get("multi_select")),
            }
        )
    return out


async def _find_or_create_row(
    run_id: str, session_id: str, tool_call_id: str, payload: dict[str, Any]
) -> tuple[str, bool]:
    """按 (run, tool_call_id) 复用 consult 行；返回 `(interrupt_id, 是否已决)`。

    已决 = resume 重放：调用方据此跳过发事件与改状态（卡片不再弹第二次）。
    """
    from sqlalchemy import select

    from app.db.base import new_id, utcnow
    from app.db.models import AgentInterrupt

    async with session_scope() as db:
        rows = (
            await db.execute(
                select(AgentInterrupt).where(
                    AgentInterrupt.run_id == run_id, AgentInterrupt.type == "consult"
                )
            )
        ).scalars().all()
        for row in rows:
            try:
                prior = json.loads(row.payload_json or "{}")
            except ValueError:
                continue
            if prior.get("kind") != "ask_user":
                continue
            if tool_call_id and str(prior.get("tool_call_id") or "") == tool_call_id:
                return row.id, row.decision is not None
        interrupt_id = new_id()
        db.add(
            AgentInterrupt(
                id=interrupt_id,
                run_id=run_id,
                session_id=session_id,
                type="consult",
                tool_name="ask_user",
                payload_json=json.dumps(payload, ensure_ascii=False, default=str)[:20000],
                created_at=utcnow(),
            )
        )
        return interrupt_id, False


async def _set_status(run_id: str, session_id: str, status: str) -> None:
    from app.db.base import utcnow
    from app.db.models import AgentRun, AgentSession

    async with session_scope() as db:
        run = await db.get(AgentRun, run_id)
        if run is not None:
            run.status = status
        sess = await db.get(AgentSession, session_id)
        if sess is not None:
            sess.status = status
            sess.updated_at = utcnow()


async def _record_decision(interrupt_id: str, decision: Any) -> None:
    """工具侧补记决定（resolve 接口已落库；重放时行已决则跳过）。"""
    from app.db.base import utcnow
    from app.db.models import AgentInterrupt

    record = decision if isinstance(decision, dict) else {"choice": str(decision or "")}
    async with session_scope() as db:
        row = await db.get(AgentInterrupt, interrupt_id)
        if row is not None and row.decision is None:
            row.decision = "option"
            row.response_json = json.dumps(record, ensure_ascii=False, default=str)
            row.decided_by = "user"
            row.decided_at = utcnow()


def _answers_of(decision: Any, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """resolve 决定 → 与 questions 对齐的答案清单（缺答项给空值，不阻断续跑）。"""
    raw: list[Any] = []
    if isinstance(decision, dict):
        choice = decision.get("choice")
        if isinstance(choice, str) and choice.strip():
            try:
                parsed = json.loads(choice)
                raw = parsed if isinstance(parsed, list) else []
            except ValueError:
                raw = []
        if not raw and decision.get("text"):
            raw = [{"text": decision.get("text")}]

    answers: list[dict[str, Any]] = []
    for i, q in enumerate(questions):
        item = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        answers.append(
            {
                "question": q["question"],
                "header": q.get("header") or "",
                "selected": [str(s) for s in (item.get("selected") or []) if str(s).strip()],
                "text": str(item.get("text") or "").strip(),
            }
        )
    return answers


__all__ = ["ask_user"]
