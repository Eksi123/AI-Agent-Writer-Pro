# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.streaming
文件名: streaming.py
作者: xionggaoqing
时间: 2026-10-27
说明: LangGraph 事件 → SSE 事件映射与中断持久化（手册「二、(2) 流式」）。

    - `stream_graph`：驱动一次 `astream`，映射 message.delta / message.complete /
      tool.* / subagent.* / usage / plan.update / interrupt.request / consult.request；
    - 普通运行与门禁 G2 共用；从 runner 拆出以隔离「事件映射」与「run 生命周期」两类职责。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.agent.loop.context import AgentContext
from app.agent.loop.emitter import Emitter
from app.agent.loop.events import sse_bus
from app.agent.loop.message_sync import MessageSyncer
from app.agent.loop.middleware.write_approval import INTERRUPT_KIND
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models import AgentInterrupt, AgentRun, AgentSession
from app.db.session import session_scope

logger = get_logger(__name__)

#: stream_mode：updates 拿节点完成与中断，messages 拿 token 级增量
_STREAM_MODES = ["updates", "messages"]


@dataclass


class StreamResult:
    interrupted: bool = False
    cancelled: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    recursion_used: int = 0
    fallback_count: int = 0


async def stream_graph(
    *,
    graph: Any,
    config: dict[str, Any],
    context: AgentContext,
    stream_input: Any,
    run_id: str,
    session_id: str,
    emitter: Emitter,
    cancel_flag: dict[str, bool],
    syncer: MessageSyncer,
    user_message_id: str | None = None,
    resumed: bool = False,
) -> StreamResult:
    """驱动一次 astream 并把 LangGraph 事件映射为 SSE（普通运行与门禁 G2 共用）。"""
    result = StreamResult()
    await syncer.init_baseline(graph, config, user_message_id=user_message_id, resumed=resumed)
    active_subagents: set[str] = set()

    async for chunk in graph.astream(
        stream_input, config=config, stream_mode=_STREAM_MODES, subgraphs=True, context=context,
    ):
        namespace, mode, payload = chunk
        result.recursion_used += 1

        if cancel_flag.get("cancelled"):
            result.cancelled = True
            break

        # 子代理泳道：新 namespace 首次出现 → start；顶层 tools 节点完成 → end
        if namespace:
            sub_name = _subagent_name(namespace)
            if sub_name and sub_name not in active_subagents:
                active_subagents.add(sub_name)
                await sse_bus.publish(run_id, session_id, "subagent.start", {"subagent_name": sub_name})

        if mode == "messages":
            usage = await _handle_message_chunk(run_id, session_id, namespace, payload, emitter)
            result.tokens_in += usage[0]
            result.tokens_out += usage[1]
        elif mode == "updates":
            if not namespace and isinstance(payload, dict) and "tools" in payload and active_subagents:
                for sub_name in list(active_subagents):
                    await sse_bus.publish(run_id, session_id, "subagent.end", {"subagent_name": sub_name})
                active_subagents.clear()
            update_result = await _handle_update(run_id, session_id, namespace, payload, emitter)
            if update_result.get("interrupted"):
                result.interrupted = True
            result.fallback_count += int(update_result.get("fallback") or 0)
            await syncer.sync(graph, config)

        if result.interrupted:
            break

    # 收尾同步消息（中断时也要落库已产生的消息）
    await syncer.sync(graph, config, force=True)
    return result


# ---------------------------------------------------------------------------
# 事件映射
# ---------------------------------------------------------------------------


async def _handle_message_chunk(
    run_id: str, session_id: str, namespace: tuple, payload: Any, emitter: Emitter
) -> tuple[int, int]:
    """`stream_mode="messages"` → message.delta / subagent.delta / usage。返回 (tokens_in, tokens_out)。"""
    try:
        message, _meta = payload
    except (TypeError, ValueError):
        return 0, 0

    content = getattr(message, "content", "")
    text = content if isinstance(content, str) else ""
    msg_id = getattr(message, "id", None) or ""
    is_subagent = bool(namespace)

    if text:
        if is_subagent:
            await emitter.publish(
                "subagent.delta",
                {
                    "subagent_name": _subagent_name(namespace),
                    "message_id": msg_id,
                    "delta": text,
                },
            )
        else:
            await emitter.publish("message.delta", {"message_id": msg_id, "delta": text})

    usage = getattr(message, "usage_metadata", None)
    tokens_in = tokens_out = 0
    if usage:
        tokens_in = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        await emitter.publish(
            "usage",
            {
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "model": getattr(message, "response_metadata", {}).get("model_name")
                if isinstance(getattr(message, "response_metadata", None), dict)
                else None,
            },
        )
    return tokens_in, tokens_out


async def _handle_update(
    run_id: str, session_id: str, namespace: tuple, payload: Any, emitter: Emitter
) -> dict[str, Any]:
    """`stream_mode="updates"` → 中断检测 / 节点级 message.complete / subagent 生命周期。"""
    out: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return out

    # ---- 中断（审批 / 咨询）----
    if "__interrupt__" in payload:
        interrupts = payload["__interrupt__"] or []
        for intr in interrupts:
            await _persist_and_emit_interrupt(run_id, session_id, intr, emitter)
        out["interrupted"] = True
        return out

    is_subagent = bool(namespace)
    subagent_name = _subagent_name(namespace) if is_subagent else None

    for node_name, update in payload.items():
        if node_name == "model" and isinstance(update, dict):
            for msg in update.get("messages") or []:
                await _emit_message_complete(msg, emitter, subagent_name)

    # plan.update：write_todos 的结果由 TodoListMiddleware 写入 state["todos"]
    if "TodoListMiddleware" in payload or "write_todos" in payload:
        todos = _extract_todos(payload)
        if todos is not None:
            await emitter.publish("plan.update", {"todos": todos})

    return out


async def _emit_message_complete(message: Any, emitter: Emitter, subagent_name: str | None) -> None:
    content = getattr(message, "content", "")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    tool_calls = [
        {"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")}
        for tc in (getattr(message, "tool_calls", None) or [])
        if isinstance(tc, dict)
    ]
    usage = getattr(message, "usage_metadata", None) or {}
    data = {
        "message_id": getattr(message, "id", None),
        "content": text,
        "tool_calls": tool_calls,
        "tokens_in": int(usage.get("input_tokens") or 0),
        "tokens_out": int(usage.get("output_tokens") or 0),
    }
    if subagent_name:
        await emitter.publish("subagent.end", {"subagent_name": subagent_name, **data})
    else:
        await emitter.publish("message.complete", data)


def _extract_todos(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    for update in payload.values():
        if isinstance(update, dict) and "todos" in update:
            todos = update["todos"]
            if isinstance(todos, list):
                return [
                    {"content": t.get("content"), "status": t.get("status")}
                    for t in todos
                    if isinstance(t, dict)
                ]
    return None


def _subagent_name(namespace: tuple) -> str:
    """从 LangGraph namespace 提取子代理名。"""
    for part in namespace or ():
        text = part if isinstance(part, str) else str(getattr(part, "namespace", part))
        if "task" in text:
            # 形如 ("task:planner",) 或 ("planner:xxxx",)
            for seg in text.split(":"):
                if seg and not seg.isdigit():
                    return seg
    return str(namespace[0]) if namespace else ""


# ---------------------------------------------------------------------------
# 中断持久化
# ---------------------------------------------------------------------------


async def _persist_and_emit_interrupt(
    run_id: str, session_id: str, intr: Any, emitter: Emitter
) -> None:
    """把 LangGraph 中断落成 `agent_interrupts` 行并发事件。

    两类中断：
    - 自定义 `WriteApprovalMiddleware`：payload `{kind: "writer_approval", ...}` → type=approval
    - langchain `HumanInTheLoopMiddleware`（batched）：payload `{action_requests, review_configs}` → type=approval
    - 门禁咨询（M4）：payload `{kind: "gate_consult", ...}` → type=consult
    """
    value = getattr(intr, "value", intr)
    if not isinstance(value, dict):
        value = {"raw": str(value)}

    kind = value.get("kind")
    interrupt_id = new_id()

    if kind == "gate_consult":
        itype = "consult"
        tool_name = None
        emit_event = "consult.request"
        emit_data = {
            "interrupt_id": interrupt_id,
            "question": value.get("question"),
            "options": value.get("options") or [],
            "allow_text": bool(value.get("allow_text", True)),
            "gate_stage": value.get("gate_stage"),
        }
    elif kind == INTERRUPT_KIND:
        itype = "approval"
        tool_name = value.get("tool_name")
        emit_event = "interrupt.request"
        emit_data = {
            "interrupt_id": interrupt_id,
            "tool_name": tool_name,
            "args": value.get("args"),
            "decisions": value.get("decisions") or ["approve", "edit", "reject", "respond"],
            "risk": value.get("risk"),
            "reason": value.get("reason"),
            "langgraph_interrupt_id": getattr(intr, "id", None),
        }
    else:
        # langchain HITL batched 模式
        itype = "approval"
        requests = value.get("action_requests") or []
        tool_name = requests[0].get("name") if requests and isinstance(requests[0], dict) else None
        decisions = ["approve", "edit", "reject", "respond"]
        for cfg in value.get("review_configs") or []:
            if isinstance(cfg, dict) and cfg.get("allowed_decisions"):
                decisions = list(cfg["allowed_decisions"])
                break
        emit_event = "interrupt.request"
        emit_data = {
            "interrupt_id": interrupt_id,
            "tool_name": tool_name,
            "args": requests[0].get("args") if requests and isinstance(requests[0], dict) else None,
            "action_requests": requests,
            "decisions": decisions,
            "batched": len(requests) > 1,
            "langgraph_interrupt_id": getattr(intr, "id", None),
        }

    async with session_scope() as db:
        db.add(
            AgentInterrupt(
                id=interrupt_id,
                run_id=run_id,
                session_id=session_id,
                type=itype,
                tool_name=tool_name,
                payload_json=json.dumps(
                    {**value, "langgraph_interrupt_id": getattr(intr, "id", None)},
                    ensure_ascii=False,
                    default=str,
                )[:20000],
                created_at=utcnow(),
            )
        )
        run = await db.get(AgentRun, run_id)
        if run is not None:
            run.status = "awaiting_consult" if itype == "consult" else "awaiting_approval"
        sess = await db.get(AgentSession, session_id)
        if sess is not None:
            sess.status = "awaiting_consult" if itype == "consult" else "awaiting_approval"
            sess.updated_at = utcnow()

    await emitter.publish(emit_event, emit_data)


async def interrupt_status(run_id: str) -> str:
    async with session_scope() as db:
        run = await db.get(AgentRun, run_id)
        return run.status if run is not None else "awaiting_approval"
