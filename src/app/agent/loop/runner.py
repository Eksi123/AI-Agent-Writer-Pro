#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.runner
文件名: runner.py
作者: xionggaoqing
时间: 2026-10-27
说明: Runner：驱动 `astream` 并把 LangGraph 事件映射为 SSE 事件（手册「二、(2) 流式」）。

    职责：
    - 生命周期：创建 run → `astream` → 中断挂起 / 完成 / 失败 / 取消 → 收尾写 `agent_runs` 汇总
    - 事件映射：`run.start` / `message.delta` / `message.complete` / `tool.*` / `subagent.*` /
      `usage` / `interrupt.request` / `error` / `run.end`
    - 消息树落库：按 graph state 增量同步到 `messages`（保持 parent/branch 结构）
    - 恢复：`Command(resume=...)` 续跑同一 run（thread_id 不变，seq 续增）
    - 取消：协作式（置 cancel_flag + 取消任务），`PatchToolCallsMiddleware` 修复截断历史
    - Langfuse：挂 langchain CallbackHandler，收尾写 score 与 trace id
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop.builder import build_agent
from app.agent.loop.context import AgentContext
from app.agent.loop.emitter import Emitter
from app.agent.loop.events import sse_bus
from app.agent.loop.middleware.write_approval import INTERRUPT_KIND
from app.agent.memory import add_message, thread_id_of
from app.core.config import get_settings
from app.core.errors import ErrorCode
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models import AgentInterrupt, AgentRun, AgentSession, Novel
from app.db.session import session_scope

logger = get_logger(__name__)

#: stream_mode：updates 拿节点完成与中断，messages 拿 token 级增量
_STREAM_MODES = ["updates", "messages"]


class RunManager:
    """管理进行中的 run（进程内单例，单用户本地应用）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, dict[str, bool]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._sessions: dict[str, str] = {}

    def running_ids(self) -> list[str]:
        return [rid for rid, t in self._tasks.items() if not t.done()]

    def session_of(self, run_id: str) -> str | None:
        return self._sessions.get(run_id)

    # ---- 对外入口 ----

    async def start(
        self,
        *,
        project_id: str,
        session_id: str,
        content: str,
        model_ref: str | None = None,
        parent_id: str | None = None,
        gate_stage: str = "",
        target_chapter_id: str = "",
    ) -> str:
        """新建 run（落库 + 起后台任务），返回 run_id。对应用户发新消息。"""
        async with session_scope() as db:
            agent_session = await _require_session(db, session_id)

            # 用户消息入树（parent_id 为空时接在 active_leaf 后；传 parent_id 则形成兄弟分支）
            user_msg = await add_message(
                db,
                session_id=session_id,
                role="user",
                content=content,
                parent_id=parent_id if parent_id else agent_session.active_leaf_id,
            )

            run_id = new_id()
            db.add(
                AgentRun(
                    id=run_id,
                    session_id=session_id,
                    project_id=project_id,
                    status="pending",
                    model_ref=model_ref or agent_session.model_ref,
                    gate_stage=gate_stage or None,
                    target_chapter_id=target_chapter_id or None,
                    started_at=utcnow(),
                )
            )
            agent_session.status = "running"
            agent_session.updated_at = utcnow()
            await db.flush()
            run_model_ref = model_ref or agent_session.model_ref

        await sse_bus.open(run_id, session_id)
        self._spawn(run_id, project_id, session_id, content, run_model_ref, user_msg.id)
        return run_id

    async def resume(self, *, run_id: str, resume_value: Any) -> None:
        """中断恢复：用 `Command(resume=...)` 续跑同一 run（thread 不变，seq 续增）。"""
        async with session_scope() as db:
            run = await db.get(AgentRun, run_id)
            if run is None:
                raise ValueError(f"run 不存在：{run_id}")
            if run.status in ("completed", "failed", "cancelled"):
                raise _run_state_conflict(run)
            run.status = "running"
            project_id, session_id = run.project_id, run.session_id
            model_ref = run.model_ref

        # 重开流：finished=False，seq 续增，缓冲保留（客户端可无缝续传）
        await sse_bus.reopen(run_id, session_id)
        self._spawn(run_id, project_id, session_id, None, model_ref, None, resume_value=resume_value)

    async def cancel(self, run_id: str) -> dict[str, Any]:
        """协作式取消：置标志 → 当前工具/模型调用返回后停。"""
        flag = self._cancel_flags.get(run_id)
        if flag is not None:
            flag["cancelled"] = True

        async with session_scope() as db:
            run = await db.get(AgentRun, run_id)
            if run is None:
                return {"run_id": run_id, "cancelled": False, "reason": "run 不存在"}
            if run.status in ("completed", "failed", "cancelled"):
                return {"run_id": run_id, "cancelled": False, "reason": f"run 已结束（{run.status}）"}
            run.status = "cancelling"
            session_id = run.session_id

        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            # 给协作式取消一点时间；超时则硬取消任务（PatchToolCallsMiddleware 会修复截断历史）
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
            except (TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                task.cancel()

        async with session_scope() as db:
            run = await db.get(AgentRun, run_id)
            if run is not None and run.status == "cancelling":
                run.status = "cancelled"
                run.ended_at = utcnow()
            sess = await db.get(AgentSession, session_id)
            if sess is not None and sess.status == "running":
                sess.status = "idle"
        await sse_bus.publish(run_id, session_id, "run.end", {"status": "cancelled"})
        await sse_bus.close(run_id)
        return {"run_id": run_id, "cancelled": True}

    def is_running(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    # ---- 内部 ----

    def _spawn(
        self,
        run_id: str,
        project_id: str,
        session_id: str,
        content: str | None,
        model_ref: str | None,
        user_message_id: str | None,
        resume_value: Any = None,
    ) -> None:
        """同一 run 串行执行（防止 resolve 与进行中的 run 并发写同一 thread）。"""
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        flag = self._cancel_flags.setdefault(run_id, {"cancelled": False})
        self._sessions[run_id] = session_id
        if resume_value is not None:
            flag["cancelled"] = False

        task = asyncio.create_task(
            self._run_serialized(
                lock,
                run_id=run_id,
                project_id=project_id,
                session_id=session_id,
                content=content,
                model_ref=model_ref,
                user_message_id=user_message_id,
                resume_value=resume_value,
            ),
            name=f"agent-run-{run_id[:8]}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._cleanup(run_id))

    def _cleanup(self, run_id: str) -> None:
        self._tasks.pop(run_id, None)
        self._cancel_flags.pop(run_id, None)
        self._locks.pop(run_id, None)
        self._sessions.pop(run_id, None)

    async def _run_serialized(self, lock: asyncio.Lock, **kwargs: Any) -> None:
        async with lock:
            await _execute_run(**kwargs)


# ---------------------------------------------------------------------------
# 运行主体
# ---------------------------------------------------------------------------


async def _execute_run(
    *,
    run_id: str,
    project_id: str,
    session_id: str,
    content: str | None,
    model_ref: str | None,
    user_message_id: str | None,
    resume_value: Any = None,
) -> None:
    """一次运行（新消息 / 中断恢复 / 门禁）。异常一律收敛为 run 状态 + error 事件。"""
    cancel_flag = run_manager._cancel_flags.setdefault(run_id, {"cancelled": False})

    # 门禁运行分流：target_chapter_id 非空 → 四阶段编排（手册「二、(6)」）
    async with session_scope() as db:
        run = await db.get(AgentRun, run_id)
        is_gate = bool(run is not None and run.target_chapter_id)
    if is_gate:
        await _execute_gate_run(
            run_id=run_id,
            project_id=project_id,
            session_id=session_id,
            model_ref=model_ref,
            resume_value=resume_value,
            cancel_flag=cancel_flag,
        )
        return

    tokens_in = tokens_out = 0
    recursion_used = 0
    final_status = "completed"
    error_text: str | None = None
    fallback_count = 0

    try:
        async with session_scope() as db:
            await _mark_running(db, run_id)

        # ---- 装配模型 ----
        chat_model, resolved_ref, fallback_models = await _resolve_models(run_id, model_ref)

        async with session_scope() as db:
            if resolved_ref and resolved_ref != model_ref:
                run = await db.get(AgentRun, run_id)
                if run is not None:
                    run.model_ref = resolved_ref

        emitter = Emitter(run_id=run_id, session_id=session_id, max_context=_max_context_of(chat_model))
        context = AgentContext(
            project_id=project_id,
            session_id=session_id,
            run_id=run_id,
            model_ref=resolved_ref or "",
            emit=emitter,
            cancel_flag=cancel_flag,
        )

        # ---- recap provider（前情提要注入）----
        recap_provider = _make_recap_provider(project_id, session_id)

        async with session_scope() as db:
            settings = get_settings()
            # 会话级上下文：选中的子代理/技能（空集 = 全局默认）
            sub_names, sk_names = await _session_context_of(db, session_id)
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

        config = _graph_config(project_id, session_id, agent.get("recursion_limit", 60))
        _attach_langfuse(config, project_id=project_id, session_id=session_id, run_id=run_id)

        await sse_bus.publish(
            run_id,
            session_id,
            "run.start",
            {"model_ref": resolved_ref or "", "gate_stage": None, "resumed": resume_value is not None},
        )

        stream_input: Any
        if resume_value is not None:
            from langgraph.types import Command

            stream_input = Command(resume=resume_value)
        else:
            stream_input = {"messages": [("user", content or "")]}

        result = await _stream_graph(
            graph=graph,
            config=config,
            context=context,
            stream_input=stream_input,
            run_id=run_id,
            session_id=session_id,
            emitter=emitter,
            cancel_flag=cancel_flag,
            syncer=_MessageSyncer(session_id=session_id),
            user_message_id=user_message_id,
            resumed=resume_value is not None,
        )
        tokens_in, tokens_out = result.tokens_in, result.tokens_out
        recursion_used = result.recursion_used
        fallback_count = result.fallback_count

        if result.interrupted:
            final_status = await _interrupt_status(run_id)
        elif result.cancelled:
            final_status = "cancelled"

    except asyncio.CancelledError:
        final_status = "cancelled"
        error_text = "运行已取消"
        raise
    except Exception as exc:  # noqa: BLE001 - 运行期异常统一收敛
        final_status = "failed"
        error_code, error_text = _error_of(exc)
        logger.error("agent_run_failed", run_id=run_id, code=error_code, error=error_text)
        await sse_bus.publish(run_id, session_id, "error", {"code": error_code, "message": error_text})
    finally:
        try:
            await _finalize(
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
            logger.error("run_finalize_failed", run_id=run_id, error=str(exc))

        await _publish_run_end(run_id, session_id, final_status, tokens_in, tokens_out, error_text)
        await sse_bus.close(run_id)


def _error_of(exc: Exception) -> tuple[int, str]:
    """ApiError 保留业务错误码（如 LlmProviderError → 50201），其余归 50001。"""
    from app.core.errors import ApiError

    if isinstance(exc, ApiError):
        return exc.code, exc.message
    return ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"

async def _publish_run_end(
    run_id: str,
    session_id: str,
    final_status: str,
    tokens_in: int,
    tokens_out: int,
    error_text: str | None,
) -> None:
    if final_status in ("awaiting_approval", "awaiting_consult"):
        await sse_bus.publish(run_id, session_id, "run.end", {"status": final_status, "paused": True})
    else:
        await sse_bus.publish(
            run_id,
            session_id,
            "run.end",
            {
                "status": final_status,
                "usage": {"tokens_in": tokens_in, "tokens_out": tokens_out},
                "error": error_text,
            },
        )


def _graph_config(project_id: str, session_id: str, recursion_limit: int) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": thread_id_of(project_id, session_id)},
        "recursion_limit": recursion_limit,
    }


# ---------------------------------------------------------------------------
# 门禁运行（手册「二、(6)」）：G0/G1/G3 服务层直连 LLM，G2 进 agent 流
# ---------------------------------------------------------------------------


async def _execute_gate_run(
    *,
    run_id: str,
    project_id: str,
    session_id: str,
    model_ref: str | None,
    resume_value: Any,
    cancel_flag: dict[str, bool],
) -> None:
    from app.agent.gates.orchestrator import (
        CONSULT_G0_MISSING,
        CONSULT_G1_PRECHECK,
        CONSULT_G3_POSTCHECK,
        GateRunner,
        advance_gate,
        initial_state,
        prepare_g2,
        resolve_target,
    )
    from app.agent.gates.state import ConsultRequired, GateState

    tokens_in = tokens_out = recursion_used = fallback_count = 0
    final_status = "completed"
    error_text: str | None = None

    try:
        async with session_scope() as db:
            await _mark_running(db, run_id)
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

        chat_model, resolved_ref, fallback_models = await _resolve_models(run_id, model_ref)
        emitter = Emitter(run_id=run_id, session_id=session_id, max_context=_max_context_of(chat_model))
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
        recap_provider = _make_recap_provider(project_id, session_id)
        async with session_scope() as db:
            settings = get_settings()
            sub_names, sk_names = await _session_context_of(db, session_id)
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
        config = _graph_config(project_id, session_id, agent.get("recursion_limit", 60))
        _attach_langfuse(config, project_id=project_id, session_id=session_id, run_id=run_id)

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
                from app.agent.gates.orchestrator import build_outline_draft_prompt

                draft_prompt = build_outline_draft_prompt(state)
                syncer = _MessageSyncer(session_id=session_id)
                sresult = await _stream_graph(
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
                    final_status = await _interrupt_status(run_id)
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
                syncer = _MessageSyncer(session_id=session_id)
                is_g2_resume = g2_command is not None
                if is_g2_resume:
                    stream_input: Any = g2_command
                    g2_command = None
                else:
                    syncer.skip_human_once = True  # 生成指令是内部消息，不入树
                    stream_input = {"messages": [("user", g2_prompt)]}
                context.gate_stage = "G2"

                sresult = await _stream_graph(
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
                    final_status = await _interrupt_status(run_id)
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
        code, error_text = _error_of(exc)
        logger.error("gate_run_failed", run_id=run_id, code=code, error=error_text)
        await sse_bus.publish(run_id, session_id, "error", {"code": code, "message": error_text})
    finally:
        try:
            await _finalize(
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
        await _publish_run_end(run_id, session_id, final_status, tokens_in, tokens_out, error_text)
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
    from app.db.models import AgentInterrupt

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


@dataclass
class _StreamResult:
    interrupted: bool = False
    cancelled: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    recursion_used: int = 0
    fallback_count: int = 0


async def _stream_graph(
    *,
    graph: Any,
    config: dict[str, Any],
    context: AgentContext,
    stream_input: Any,
    run_id: str,
    session_id: str,
    emitter: Emitter,
    cancel_flag: dict[str, bool],
    syncer: _MessageSyncer,
    user_message_id: str | None = None,
    resumed: bool = False,
) -> _StreamResult:
    """驱动一次 astream 并把 LangGraph 事件映射为 SSE（普通运行与门禁 G2 共用）。"""
    result = _StreamResult()
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


async def _interrupt_status(run_id: str) -> str:
    async with session_scope() as db:
        run = await db.get(AgentRun, run_id)
        return run.status if run is not None else "awaiting_approval"


# ---------------------------------------------------------------------------
# 消息树同步
# ---------------------------------------------------------------------------


class _MessageSyncer:
    """把 graph state 的 messages 增量同步到业务库 `messages`（保持树结构）。

    ★按**索引基线**增量同步（不按消息 id 匹配）：graph state 里的消息 id 由 LangGraph 生成，
    与业务库 uuid 无对应关系；而 state 的 messages 是**追加式**的，故只需记录「已同步到的下标」，
    之后每次 sync 只处理新增部分。

    基线确定：
    - 首轮：用户消息已由 API 层落库，state 里它将成为 messages[0] → 基线 = 1，parent = 该消息 id；
    - 恢复：中断前的消息已全部落库 → 基线 = 当前 state 长度，parent = 会话 active_leaf_id。
    """

    def __init__(self, *, session_id: str) -> None:
        self.session_id = session_id
        self._index = 0
        self._last_leaf: str | None = None
        #: 门禁 G2：跳过下一条 human 消息（内部生成指令不入消息树）
        self.skip_human_once = False

    async def init_baseline(
        self,
        graph: Any,
        config: dict[str, Any],
        *,
        user_message_id: str | None,
        resumed: bool,
    ) -> None:
        """确定同步基线与父节点。"""
        if resumed:
            self._index = await self._state_len(graph, config)
            self._last_leaf = await self._active_leaf()
        else:
            # 首轮：state 尚无 checkpoint（长度 0），我们的用户消息将成为 messages[0]
            self._index = 1 if user_message_id else await self._state_len(graph, config)
            self._last_leaf = user_message_id

    async def sync(self, graph: Any, config: dict[str, Any], *, force: bool = False) -> None:
        try:
            state = await graph.aget_state(config)
        except Exception as exc:  # pragma: no cover
            logger.debug("state_read_failed", error=str(exc))
            return

        messages = (state.values or {}).get("messages") or []
        new_messages = messages[self._index :]
        if not new_messages:
            return

        # 门禁内部指令消息不入树（只推进指针）
        if self.skip_human_once and _role_of(new_messages[0]) == "user":
            new_messages = new_messages[1:]
            self._index += 1
            self.skip_human_once = False
            if not new_messages:
                return

        # 未完成的流式消息不落库（留待下次 sync）
        if not force and _is_incomplete(new_messages[-1]):
            new_messages = new_messages[:-1]
        if not new_messages:
            return

        async with session_scope() as db:
            written = 0
            for msg in new_messages:
                content = getattr(msg, "content", "")
                tool_calls = [
                    {"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")}
                    for tc in (getattr(msg, "tool_calls", None) or [])
                    if isinstance(tc, dict)
                ]
                usage = getattr(msg, "usage_metadata", None) or {}
                try:
                    row = await add_message(
                        db,
                        session_id=self.session_id,
                        role=_role_of(msg),
                        content=content
                        if isinstance(content, str)
                        else json.dumps(content, ensure_ascii=False, default=str),
                        parent_id=self._last_leaf,
                        tool_calls=tool_calls or None,
                        status=_status_of(msg),
                        tokens_in=int(usage.get("input_tokens") or 0),
                        tokens_out=int(usage.get("output_tokens") or 0),
                        model=_model_of(msg),
                    )
                    self._last_leaf = row.id
                    written += 1
                except Exception as exc:
                    logger.warning("message_sync_failed", error=str(exc))
            self._index += len(new_messages)
            logger.debug("messages_synced", session_id=self.session_id, count=written)

    @staticmethod
    async def _state_len(graph: Any, config: dict[str, Any]) -> int:
        try:
            state = await graph.aget_state(config)
            return len((state.values or {}).get("messages") or [])
        except Exception:  # pragma: no cover - 首轮无 checkpoint
            return 0

    async def _active_leaf(self) -> str | None:
        async with session_scope() as db:
            sess = await db.get(AgentSession, self.session_id)
            return sess.active_leaf_id if sess is not None else None


def _is_incomplete(msg: Any) -> bool:
    """流式中途的消息（无内容且无 tool_calls）视为未完成。"""
    if getattr(msg, "status", None) == "streaming":
        return True
    return not getattr(msg, "content", "") and not getattr(msg, "tool_calls", None)


def _role_of(msg: Any) -> str:
    mtype = getattr(msg, "type", "") or ""
    return {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}.get(mtype, "assistant")


def _status_of(msg: Any) -> str:
    status = getattr(msg, "status", None)
    if status in ("error", "cancelled"):
        return str(status)
    return "complete"


def _model_of(msg: Any) -> str | None:
    meta = getattr(msg, "response_metadata", None)
    if isinstance(meta, dict):
        return meta.get("model_name") or meta.get("model")
    return None


# ---------------------------------------------------------------------------
# 模型解析与收尾
# ---------------------------------------------------------------------------


async def _resolve_models(run_id: str, model_ref: str | None) -> tuple[Any, str | None, list[Any]]:
    """解析主模型 + 降级链。未配置模型时抛 LlmProviderError（由上层转 error 事件）。"""
    from app.agent.llm.factory import get_chat_model, get_default_chat_model, get_fallback_chain

    async with session_scope() as db:
        if model_ref:
            chat_model = await get_chat_model(db, model_ref)
            chain = await get_fallback_chain(db, model_ref)
        else:
            model_ref, chat_model = await get_default_chat_model(db)
            chain = await get_fallback_chain(db, model_ref)

        fallbacks: list[Any] = []
        for ref in chain[1:4]:  # 主模型之外最多 3 级降级
            try:
                fallbacks.append(await get_chat_model(db, ref))
            except Exception as exc:
                logger.warning("fallback_model_skip", model_ref=ref, error=str(exc))
        return chat_model, model_ref, fallbacks


def _max_context_of(model: Any) -> int:
    for attr in ("max_context", "max_tokens", "context_window"):
        value = getattr(model, attr, None)
        if isinstance(value, int) and value > 0:
            return value
    return 32768


def _attach_langfuse(config: dict[str, Any], **metadata: Any) -> None:
    """挂 langchain CallbackHandler（未配置 Key 时自动跳过）。"""
    from app.agent.tracing import build_callback_handler

    handler = build_callback_handler(**metadata)
    if handler is not None:
        config["callbacks"] = [handler]


def _make_recap_provider(project_id: str, session_id: str) -> Any:
    async def _provider(_context: Any) -> str:
        from app.agent.knowledge import build_recap

        async with session_scope() as db:
            recap = await build_recap(db, project_id)
            # 会话级「已读章节」上下文 Chips（手册「三、(3) 输入窗口」）
            pinned = await _pinned_chapters_of(db, session_id, project_id)
            if pinned:
                recap = f"{recap}\n\n{pinned}" if recap else pinned
            return recap

    return _provider


async def _pinned_chapters_of(db: AsyncSession, session_id: str, project_id: str) -> str:
    from app.agent.knowledge import build_pinned_chapters
    from app.db.models import AgentSession

    sess = await db.get(AgentSession, session_id)
    if sess is None or not sess.context_chapter_ids_json:
        return ""
    try:
        ids = json.loads(sess.context_chapter_ids_json)
    except ValueError:
        return ""
    if not isinstance(ids, list) or not ids:
        return ""
    try:
        return await build_pinned_chapters(db, project_id, [str(x) for x in ids])
    except Exception as exc:  # 上下文注入失败不阻断运行
        logger.warning("pinned_chapters_failed", session_id=session_id, error=str(exc))
        return ""


async def _session_context_of(db: AsyncSession, session_id: str) -> tuple[set[str], set[str]]:
    """读会话级子代理/技能选择，把 id 解析为 name（空集 = 用全局默认，不额外过滤）。

    - 子代理 id → `subagent_configs.name`；
    - 技能 id 可为 DB uuid（用户技能 → name）或 `builtin:{name}`（内置技能 → name）。
    """
    from app.db.models import AgentSession, SubagentConfig

    sess = await db.get(AgentSession, session_id)
    if sess is None:
        return set(), set()

    def _parse_ids(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except ValueError:
            return []
        return [str(x) for x in value] if isinstance(value, list) else []

    sub_ids = _parse_ids(sess.subagent_ids_json)
    skill_ids = _parse_ids(sess.skill_ids_json)

    sub_names: set[str] = set()
    if sub_ids:
        rows = (
            await db.execute(select(SubagentConfig.name).where(SubagentConfig.id.in_(sub_ids)))
        ).scalars().all()
        sub_names = set(rows)

    skill_names: set[str] = set()
    for sid in skill_ids:
        if sid.startswith("builtin:"):
            skill_names.add(sid.split(":", 1)[1])
        else:
            from app.db.models import SkillConfig

            name = (
                await db.execute(select(SkillConfig.name).where(SkillConfig.id == sid))
            ).scalar_one_or_none()
            if name:
                skill_names.add(name)

    return sub_names, skill_names


async def _mark_running(db: AsyncSession, run_id: str) -> None:
    run = await db.get(AgentRun, run_id)
    if run is not None and run.status in ("pending", "cancelling"):
        run.status = "running"


async def _finalize(
    *,
    run_id: str,
    session_id: str,
    project_id: str,
    status: str,
    tokens_in: int,
    tokens_out: int,
    recursion_used: int,
    fallback_count: int,
    error: str | None,
) -> None:
    """运行收尾：写 `agent_runs` 汇总 + 会话状态 + Langfuse score + 派生索引。

    手册「二、(2)」：收尾不在中间件里做（无 on_run_end 钩子），由 runner 统一执行。
    """
    from app.agent.tracing import create_score, current_trace_id

    paused = status in ("awaiting_approval", "awaiting_consult")

    async with session_scope() as db:
        run = await db.get(AgentRun, run_id)
        if run is not None:
            run.status = status
            run.tokens_in = tokens_in
            run.tokens_out = tokens_out
            run.recursion_used = recursion_used
            run.fallback_count = fallback_count
            run.error = error
            if not paused:
                run.ended_at = utcnow()
            trace_id = current_trace_id()
            if trace_id:
                run.langfuse_trace_id = trace_id

        if not paused:
            sess = await db.get(AgentSession, session_id)
            if sess is not None:
                sess.status = "error" if status == "failed" else "idle"
                sess.updated_at = utcnow()

    if not paused:
        # 字数统计刷新（Agent 可能写了正文）
        try:
            async with session_scope() as db:
                from app.services import novels as novels_service

                await novels_service.refresh_project_index(db, project_id)
        except Exception as exc:  # pragma: no cover
            logger.debug("project_index_refresh_failed", error=str(exc))

        try:
            trace_id = current_trace_id()
            word_count = await _project_word_count(project_id)
            if trace_id:
                create_score("run_tokens", tokens_in + tokens_out, trace_id=trace_id)
                create_score("project_words", word_count, trace_id=trace_id)
                create_score("fallback_used", 1 if fallback_count else 0, trace_id=trace_id)
        except Exception as exc:  # pragma: no cover
            logger.debug("langfuse_score_skipped", error=str(exc))


async def _project_word_count(project_id: str) -> int:
    async with session_scope() as db:
        from app.db.models import ProjectIndex

        row = await db.get(ProjectIndex, project_id)
        return int(row.total_words) if row is not None else 0


def _run_state_conflict(run: AgentRun) -> Any:
    from app.core.errors import ApiError

    return ApiError(
        f"运行已结束（{run.status}），无法继续操作",
        code=ErrorCode.RUN_STATE_CONFLICT,
        data={"run_id": run.id, "status": run.status},
    )


async def _require_session(db: AsyncSession, session_id: str) -> AgentSession:
    sess = await db.get(AgentSession, session_id)
    if sess is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"会话不存在：{session_id}")
    return sess


#: 进程内单例
run_manager = RunManager()


async def list_sessions(db: AsyncSession, project_id: str | None = None) -> list[dict[str, Any]]:
    """会话列表（按项目隔离）。

    DTO 复用 `services.agent._session_dto`，确保与单会话读写返回同构
    （含 subagent_ids / skill_ids / context_chapter_ids 上下文 Chips）。
    """
    from app.services.agent import _session_dto

    stmt = select(AgentSession).order_by(AgentSession.updated_at.desc())
    if project_id:
        stmt = stmt.where(AgentSession.project_id == project_id)
    rows = (await db.execute(stmt)).scalars().all()

    novel_titles: dict[str, str] = {}
    result = []
    for s in rows:
        if s.project_id not in novel_titles:
            novel = await db.get(Novel, s.project_id)
            novel_titles[s.project_id] = novel.title if novel else ""
        result.append(_session_dto(s, project_title=novel_titles[s.project_id]))
    return result


async def list_tool_calls(db: AsyncSession, session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    from app.db.models import ToolCallLog

    rows = (
        (
            await db.execute(
                select(ToolCallLog)
                .where(ToolCallLog.session_id == session_id)
                .order_by(ToolCallLog.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "run_id": r.run_id,
            "tool_name": r.tool_name,
            "status": r.status,
            "duration_ms": r.duration_ms,
            "error": r.error,
            "result_excerpt": (r.result_excerpt or "")[:200],
            "created_at": r.created_at,
        }
        for r in rows
    ]
