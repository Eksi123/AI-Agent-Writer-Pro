# -*- coding: utf-8 -*-
"""
模块: app.agent.gates.runner
文件名: runner.py
作者: xionggaoqing
时间: 2026-10-27
说明: 门禁运行编排：驱动 G0→G1→G2→G3 状态机并把 G2 接入 agent 流（手册「二、(6)」）。

    与 `gates/orchestrator.py`（状态机判定）同住 gates/：门禁运行编排与状态机判定
    属同一功能，不应跨子包劈成两半（本模块原先住在 loop/runner.py）。

    G0/G1/G3 由 orchestrator 服务层直连 LLM；G2 走 `loop.streaming.stream_graph`。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.agent.gates.orchestrator import (
    CONSULT_G0_MISSING,
    CONSULT_G1_PRECHECK,
    CONSULT_G3_POSTCHECK,
    GateRunner,
    advance_gate,
    build_outline_draft_prompt,
    initial_state,
    prepare_g2,
    resolve_target,
)
from app.agent.gates.state import ConsultRequired, GateState
from app.agent.loop.builder import build_agent
from app.agent.loop.context import AgentContext
from app.agent.loop.emitter import Emitter
from app.agent.loop.events import sse_bus
from app.agent.loop.lifecycle import (
    error_of,
    finalize,
    graph_config,
    mark_running,
    publish_run_end,
)
from app.agent.loop.message_sync import MessageSyncer
from app.agent.loop.resolve import (
    attach_langfuse,
    make_recap_provider,
    max_context_of,
    resolve_models,
    session_context_of,
)
from app.agent.loop.streaming import interrupt_status, stream_graph
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models import AgentInterrupt, AgentRun, AgentSession
from app.db.session import session_scope

logger = get_logger(__name__)


async def execute_gate_run(
    *,
    run_id: str,
    project_id: str,
    session_id: str,
    model_ref: str | None,
    resume_value: Any,
    cancel_flag: dict[str, bool],
) -> None:
    tokens_in = tokens_out = recursion_used = fallback_count = 0
    final_status = "completed"
    error_text: str | None = None

    try:
        async with session_scope() as db:
            await mark_running(db, run_id)
            run = await db.get(AgentRun, run_id)
            if run is None:
                raise RuntimeError(f"run 不存在：{run_id}")
            target_chapter_id = run.target_chapter_id or ""
            # 恢复已有门禁状态；否则新建
            if resume_value is not None and run.gate_result_json:
                state = GateState.from_json(run.gate_result_json)
            else:
                target = await resolve_target(db, project_id, target_chapter_id)
                state = initial_state(target, word_target=get_settings().agent_word_target)
                if run.gate_stage:
                    state.stage = run.gate_stage

        chat_model, resolved_ref, fallback_models = await resolve_models(run_id, model_ref)
        emitter = Emitter(run_id=run_id, session_id=session_id, max_context=max_context_of(chat_model))
        context = AgentContext(
            project_id=project_id,
            session_id=session_id,
            run_id=run_id,
            model_ref=resolved_ref or "",
            emit=emitter,
            cancel_flag=cancel_flag,
            gate_stage=state.stage,
        )

        # G2 agent 装配（懒用到，但先建好：writer 子代理需要）
        recap_provider = make_recap_provider(project_id, session_id)
        async with session_scope() as db:
            settings = get_settings()
            sub_names, sk_names = await session_context_of(db, session_id)
            agent = await build_agent(
                db,
                project_id=project_id,
                model=chat_model,
                fallback_models=fallback_models,
                recap_provider=recap_provider,
                subagents_enabled=settings.agent_subagents_enabled,
                recursion_limit=settings.agent_recursion_limit,
                session_id=session_id,
                subagent_names=sub_names or None,
                skill_names=sk_names or None,
            )
            graph = agent["graph"]
        config = graph_config(project_id, session_id, agent.get("recursion_limit", 60))
        attach_langfuse(config, project_id=project_id, session_id=session_id, run_id=run_id)

        await sse_bus.publish(
            run_id,
            session_id,
            "run.start",
            {"model_ref": resolved_ref or "", "gate_stage": state.stage, "resumed": resume_value is not None},
        )

        # resume 决策：consult → 门禁 resume；approval → graph Command（由 G2 流内处理）
        resume_decision: dict[str, Any] | None = None
        g2_command: Any = None
        if resume_value is not None:
            if state.pending_consult in (CONSULT_G0_MISSING, CONSULT_G1_PRECHECK, CONSULT_G3_POSTCHECK):
                resume_decision = _extract_consult_decision(resume_value)
            else:
                from langgraph.types import Command

                g2_command = Command(resume=resume_value)

        runner = GateRunner(
            db=None,  # type: ignore[arg-type]  # 每次操作用独立 session_scope
            state=state,
            validator_model=chat_model,
            graph=graph,
            graph_config=config,
            emit=emitter,
            run_id=run_id,
        )

        # 门禁状态机循环：advance → (G2 需要 agent 流) → advance ... 直到 done/failed/consult
        while True:
            if cancel_flag.get("cancelled"):
                final_status = "cancelled"
                break

            try:
                async with session_scope() as db:
                    runner.db = db
                    state = await advance_gate(runner, resume_decision=resume_decision)
                    await _persist_gate(db, run_id, state)
                resume_decision = None
            except ConsultRequired as consult:
                await _persist_gate_consult(run_id, session_id, state, consult)
                final_status = "awaiting_consult"
                break

            if state.stage == "G0" and state.outcome == "agent_write_outline":
                # 让 Agent 代写大纲小节（一次 agent 流），完成后重查 G0
                draft_prompt = build_outline_draft_prompt(state)
                syncer = MessageSyncer(session_id=session_id)
                sresult = await stream_graph(
                    graph=graph,
                    config=config,
                    context=context,
                    stream_input={"messages": [("user", draft_prompt)]},
                    run_id=run_id,
                    session_id=session_id,
                    emitter=emitter,
                    cancel_flag=cancel_flag,
                    syncer=syncer,
                    resumed=False,
                )
                tokens_in += sresult.tokens_in
                tokens_out += sresult.tokens_out
                recursion_used += sresult.recursion_used
                fallback_count += sresult.fallback_count
                if sresult.cancelled:
                    final_status = "cancelled"
                    break
                if sresult.interrupted:
                    final_status = await interrupt_status(run_id)
                    break
                # 大纲已代写 → 重查 G0
                state.outcome = ""
                state.stage = "G0"
                async with session_scope() as db:
                    await _persist_gate(db, run_id, state)
                continue

            if state.stage == "G2":
                # G2：agent 流式生成正文
                g2_prompt = await prepare_g2(runner)
                syncer = MessageSyncer(session_id=session_id)
                is_g2_resume = g2_command is not None
                if is_g2_resume:
                    stream_input: Any = g2_command
                    g2_command = None
                else:
                    syncer.skip_human_once = True  # 生成指令是内部消息，不入树
                    stream_input = {"messages": [("user", g2_prompt)]}
                context.gate_stage = "G2"

                sresult = await stream_graph(
                    graph=graph,
                    config=config,
                    context=context,
                    stream_input=stream_input,
                    run_id=run_id,
                    session_id=session_id,
                    emitter=emitter,
                    cancel_flag=cancel_flag,
                    syncer=syncer,
                    resumed=is_g2_resume,
                )
                tokens_in += sresult.tokens_in
                tokens_out += sresult.tokens_out
                recursion_used += sresult.recursion_used
                fallback_count += sresult.fallback_count

                if sresult.cancelled:
                    final_status = "cancelled"
                    break
                if sresult.interrupted:
                    # G2 触发了写入审批（整章覆盖等）→ 暂停等待审批
                    async with session_scope() as db:
                        await _persist_gate(db, run_id, state)
                    final_status = await interrupt_status(run_id)
                    break
                # G2 生成结束 → 进入 G3 后检
                state.stage = "G3"
                async with session_scope() as db:
                    await _persist_gate(db, run_id, state)
                continue

            if state.stage in ("done", "failed"):
                final_status = "completed" if state.stage == "done" else "failed"
                if state.stage == "failed" and state.outcome.startswith("cancelled"):
                    final_status = "cancelled"
                break

    except asyncio.CancelledError:
        final_status = "cancelled"
        error_text = "运行已取消"
        raise
    except Exception as exc:  # noqa: BLE001
        final_status = "failed"
        code, error_text = error_of(exc)
        logger.error("gate_run_failed", run_id=run_id, code=code, error=error_text)
        await sse_bus.publish(run_id, session_id, "error", {"code": code, "message": error_text})
    finally:
        try:
            await finalize(
                run_id=run_id,
                session_id=session_id,
                project_id=project_id,
                status=final_status,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                recursion_used=recursion_used,
                fallback_count=fallback_count,
                error=error_text,
            )
        except Exception as exc:  # pragma: no cover
            logger.error("gate_finalize_failed", run_id=run_id, error=str(exc))
        await publish_run_end(run_id, session_id, final_status, tokens_in, tokens_out, error_text)
        await sse_bus.close(run_id)


def _extract_consult_decision(resume_value: Any) -> dict[str, Any]:
    """从 consult resume 值提取决策（resolve 接口已归一化为 {decision, choice, text}）。"""
    if isinstance(resume_value, dict):
        return resume_value
    return {"choice": str(resume_value)}


async def _persist_gate(db: Any, run_id: str, state: Any) -> None:
    """把门禁状态写回 agent_runs（gate_stage / gate_retry_count / gate_result_json）。"""
    run = await db.get(AgentRun, run_id)
    if run is None:
        return
    run.gate_stage = state.stage if state.stage in ("G0", "G1", "G2", "G3") else None
    run.gate_retry_count = state.g3_retry
    run.gate_result_json = state.to_json()


async def _persist_gate_consult(run_id: str, session_id: str, state: Any, consult: Any) -> None:
    """门禁咨询：落 agent_interrupts(type=consult) + 发 consult.request + 置状态。"""
    interrupt_id = new_id()
    payload = {
        "kind": "gate_consult",
        "gate_stage": state.stage,
        "consult_kind": consult.kind,
        **consult.payload,
    }
    async with session_scope() as db:
        db.add(
            AgentInterrupt(
                id=interrupt_id,
                run_id=run_id,
                session_id=session_id,
                type="consult",
                tool_name=None,
                payload_json=json.dumps(payload, ensure_ascii=False, default=str)[:20000],
                created_at=utcnow(),
            )
        )
        run = await db.get(AgentRun, run_id)
        if run is not None:
            run.status = "awaiting_consult"
            run.gate_stage = state.stage if state.stage in ("G0", "G1", "G2", "G3") else None
            run.gate_retry_count = state.g3_retry
            # 记录 pending_consult 供 resume 路由
            state.pending_consult = consult.kind
            run.gate_result_json = state.to_json()
        sess = await db.get(AgentSession, session_id)
        if sess is not None:
            sess.status = "awaiting_consult"
            sess.updated_at = utcnow()

    await sse_bus.publish(
        run_id,
        session_id,
        "consult.request",
        {
            "interrupt_id": interrupt_id,
            "question": consult.payload.get("question", ""),
            "options": consult.payload.get("options", []),
            "allow_text": consult.payload.get("allow_text", True),
            "gate_stage": state.stage,
        },
    )
