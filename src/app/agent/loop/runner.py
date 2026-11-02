# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.runner
文件名: runner.py
作者: xionggaoqing
时间: 2026-10-27
说明: Runner：run 生命周期管理（手册「二、(2) 流式」）。

    职责（单一）：创建 run → 起后台任务 → 中断挂起 / 完成 / 失败 / 取消 → 收尾。
    - 事件映射在 `loop/streaming.py`；消息树落库在 `loop/message_sync.py`；
    - 模型与会话上下文解析在 `loop/resolve.py`；收尾与状态工具在 `loop/lifecycle.py`；
    - 流程四阶段编排在 `chapter_workflow/runner.py`。
    （本模块只承担 run 生命周期；其余职责已按上述拆出，避免上帝文件）

    - 恢复：`Command(resume=...)` 续跑同一 run（thread_id 不变，seq 续增）
    - 取消：协作式（置 cancel_flag + 取消任务），`PatchToolCallsMiddleware` 修复截断历史
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.agent.loop.builder import build_agent
from app.agent.loop.context import AgentContext
from app.agent.loop.lifecycle import (
    error_of,
    finalize,
    graph_config,
    mark_running,
    publish_run_end,
    run_state_conflict,
)
from app.agent.loop.resolve import (
    attach_langfuse,
    make_recap_provider,
    max_context_of,
    resolve_models,
    session_context_of,
)
from app.agent.loop.streaming import interrupt_status, stream_graph
from app.agent.tracing.messages.message_sync import MessageSyncer
from app.agent.tracing.messages.tree import add_message, history_messages_for_seed
from app.agent.tracing.sse.emitter import Emitter
from app.agent.tracing.sse.events import sse_bus
from app.agent.workflows.writing_steps.runner import execute_chapter_workflow
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models import AgentRun, AgentSession, Message
from app.db.session import session_scope
from app.services import agent as agent_service

logger = get_logger(__name__)


class RunManager:
    """管理进行中的 run（进程内单例，单用户本地应用）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, dict[str, bool]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._sessions: dict[str, str] = {}
        #: v1028：运行中「通过消息引导发送」的引导消息队列（run 级，中间件每次模型调用前取用）
        self._steer_queues: dict[str, list[str]] = {}
        #: ★v1101：run 级隐藏上下文（选区润色提示词等）——只注入模型请求，不落库不展示
        self._hidden_contexts: dict[str, str] = {}

    def running_ids(self) -> list[str]:
        return [rid for rid, t in self._tasks.items() if not t.done()]

    def session_of(self, run_id: str) -> str | None:
        return self._sessions.get(run_id)

    # ---- v1028：引导消息（steer） ----

    def push_steer(self, run_id: str, text: str) -> None:
        """运行中追加引导消息（SteerInjectionMiddleware 在下次模型调用前注入）。"""
        self._steer_queues.setdefault(run_id, []).append(text)

    def take_steer(self, run_id: str) -> list[str]:
        """取出并清空该 run 的引导消息（中间件调用）。"""
        return self._steer_queues.pop(run_id, [])

    # ---- ★v1101：run 级隐藏上下文（润色提示词等，不入消息树/不展示） ----

    def set_hidden_context(self, run_id: str, text: str) -> None:
        if text:
            self._hidden_contexts[run_id] = text

    def hidden_context_of(self, run_id: str) -> str:
        """读取该 run 的隐藏上下文（中间件每次模型调用读取；run 结束随 _cleanup 清除）。"""
        return self._hidden_contexts.get(run_id, "")

    # ---- 对外入口 ----

    async def start(
        self,
        *,
        project_id: str,
        session_id: str,
        content: str,
        model_ref: str | None = None,
        parent_id: str | None = None,
        workflow_stage: str = "",
        target_chapter_id: str = "",
        hidden_context: str = "",
    ) -> str:
        """新建 run（落库 + 起后台任务），返回 run_id。对应用户发新消息。

        ★v1101：`hidden_context`（如选区润色提示词）只注入模型请求，不入消息树、不展示。
        """
        async with session_scope() as db:
            agent_session = await agent_service.require_session(db, session_id)

            # v1028：首条用户消息 → 会话标题自动改为「问题总结」（此前无 user 消息时才改）
            has_user_msg = (
                await db.execute(
                    select(Message).where(Message.session_id == session_id, Message.role == "user").limit(1)
                )
            ).scalars().first()
            if has_user_msg is None:
                agent_session.title = agent_service.summarize_session_title(content)

            # 用户消息入树（parent_id 为空时接在 active_leaf 后；传 parent_id 则形成兄弟分支）
            user_msg = await add_message(
                db,
                session_id=session_id,
                role="user",
                content=content,
                parent_id=parent_id if parent_id else agent_session.active_leaf_id,
            )

            run_id = new_id()
            self.set_hidden_context(run_id, hidden_context)
            db.add(
                AgentRun(
                    id=run_id,
                    session_id=session_id,
                    project_id=project_id,
                    status="pending",
                    model_ref=model_ref or agent_session.model_ref,
                    workflow_stage=workflow_stage or None,
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
                raise run_state_conflict(run)
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
        # ★v1101：取消同样释放「AI 编辑锁」（finalize 不会为取消路径执行）
        try:
            from app.services.content_lock import content_locks

            freed = await content_locks.release_for_run(run_id)
            for cid in freed:
                await sse_bus.publish(
                    run_id, session_id, "content.lock", {"chapter_id": cid, "run_id": run_id, "locked": False}
                )
        except Exception as exc:  # pragma: no cover
            logger.warning("content_lock_release_on_cancel_failed", run_id=run_id, error=str(exc))
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
        self._hidden_contexts.pop(run_id, None)
        self._steer_queues.pop(run_id, None)

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
    """一次运行（新消息 / 中断恢复 / 流程）。异常一律收敛为 run 状态 + error 事件。"""
    cancel_flag = run_manager._cancel_flags.setdefault(run_id, {"cancelled": False})

    # 固定流程运行分流：target_chapter_id 非空 → 四阶段编排（手册「二、(6)」）
    async with session_scope() as db:
        run = await db.get(AgentRun, run_id)
        is_workflow_run = bool(run is not None and run.target_chapter_id)
    if is_workflow_run:
        await execute_chapter_workflow(
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
            await mark_running(db, run_id)

        # ---- 装配模型 ----
        chat_model, resolved_ref, fallback_models = await resolve_models(run_id, model_ref)

        async with session_scope() as db:
            if resolved_ref and resolved_ref != model_ref:
                run = await db.get(AgentRun, run_id)
                if run is not None:
                    run.model_ref = resolved_ref

        emitter = Emitter(run_id=run_id, session_id=session_id, max_context=max_context_of(chat_model))
        context = AgentContext(
            project_id=project_id,
            session_id=session_id,
            run_id=run_id,
            model_ref=resolved_ref or "",
            emit=emitter,
            cancel_flag=cancel_flag,
        )

        # ---- recap provider（前情提要注入）----
        recap_provider = make_recap_provider(project_id, session_id)

        async with session_scope() as db:
            settings = get_settings()
            # 会话级上下文：选中的子代理/技能（空集 = 全局默认）
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
                # ★v1101：run 级隐藏上下文（润色提示词等）请求级注入，不落库不展示
                hidden_context_provider=run_manager.hidden_context_of,
            )
            graph = agent["graph"]

        config = graph_config(project_id, session_id, agent.get("recursion_limit", 60))
        attach_langfuse(config, project_id=project_id, session_id=session_id, run_id=run_id)

        # ★v1101：checkpoint 为空（进程重启 / 清理）时从业务库回填会话历史，
        # 保证 Agent 不「失忆」；回填内容只进 state，不会被重复落库（基线=state 长度）。
        await seed_history_if_empty(graph, config, session_id, exclude_id=user_message_id)

        await sse_bus.publish(
            run_id,
            session_id,
            "run.start",
            {"model_ref": resolved_ref or "", "workflow_stage": None, "resumed": resume_value is not None},
        )

        stream_input: Any
        if resume_value is not None:
            from langgraph.types import Command

            stream_input = Command(resume=resume_value)
        else:
            stream_input = {"messages": [("user", content or "")]}

        result = await stream_graph(
            graph=graph,
            config=config,
            context=context,
            stream_input=stream_input,
            run_id=run_id,
            session_id=session_id,
            emitter=emitter,
            cancel_flag=cancel_flag,
            syncer=MessageSyncer(session_id=session_id),
            user_message_id=user_message_id,
            resumed=resume_value is not None,
        )
        tokens_in, tokens_out = result.tokens_in, result.tokens_out
        recursion_used = result.recursion_used
        fallback_count = result.fallback_count

        if result.interrupted:
            final_status = await interrupt_status(run_id)
        elif result.cancelled:
            final_status = "cancelled"

    except asyncio.CancelledError:
        final_status = "cancelled"
        error_text = "运行已取消"
        raise
    except Exception as exc:  # noqa: BLE001 - 运行期异常统一收敛
        final_status = "failed"
        error_code, error_text = error_of(exc)
        logger.error("agent_run_failed", run_id=run_id, code=error_code, error=error_text)
        await sse_bus.publish(run_id, session_id, "error", {"code": error_code, "message": error_text})
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
            logger.error("run_finalize_failed", run_id=run_id, error=str(exc))

        await publish_run_end(run_id, session_id, final_status, tokens_in, tokens_out, error_text)
        await sse_bus.close(run_id)


#: 进程内单例
run_manager = RunManager()


async def seed_history_if_empty(
    graph: Any, config: dict[str, Any], session_id: str, *, exclude_id: str | None = None
) -> None:
    """★v1101：checkpoint 为空时从业务库回填会话历史（进程重启 / 清理后不「失忆」）。

    回填只写 graph state（`as_node="__start__"` 不触发任何节点）；MessageSyncer 的
    基线 = 回填后的 state 长度，故历史不会被重复落库。checkpoint 已有内容时直接跳过。
    """
    try:
        state = await graph.aget_state(config)
    except Exception as exc:  # pragma: no cover
        logger.debug("seed_state_read_failed", error=str(exc))
        return
    if state is not None and (state.values or {}).get("messages"):
        return
    async with session_scope() as db:
        history = await history_messages_for_seed(db, session_id, exclude_id=exclude_id)
    if not history:
        return
    try:
        await graph.aupdate_state(config, {"messages": history}, as_node="__start__")
        logger.info("history_seeded", session_id=session_id, messages=len(history))
    except Exception as exc:  # pragma: no cover - 回填失败不阻断本轮运行
        logger.warning("history_seed_failed", session_id=session_id, error=str(exc))
