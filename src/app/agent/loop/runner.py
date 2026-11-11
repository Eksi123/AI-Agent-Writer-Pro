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
import time
from typing import Any

from langgraph.errors import GraphRecursionError
from sqlalchemy import select

from app.agent.loop.builder import build_agent
from app.agent.loop.context import AgentContext
from app.agent.loop.lifecycle import (
    error_of,
    finalize,
    graph_config,
    mark_running,
    publish_run_end,
    resume_input_for,
    run_state_conflict,
)
from app.agent.loop.netprobe import classify_failure, notice_text_for, probe_provider
from app.agent.loop.resolve import (
    attach_langfuse,
    make_outline_ctx_provider,
    make_recap_provider,
    max_context_of,
    resolve_models,
    session_context_of,
)
from app.agent.loop.streaming import StreamResult, interrupt_status, stream_graph
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

#: 全局单飞：同一时刻只允许一个会话处于「AI 思考执行中」。
#: 其它会话发消息时不排队不报错——AI 直接以该文案回复（用户消息与回复均落消息树）。
GLOBAL_BUSY_REPLY = "当前已有其他会话在进行AI思考哦，等任务完成后再聊吧~"

#: 网络/模型接口异常后的宽限窗口（秒）与探测间隔（秒）：
#: 窗口内每间隔探测一次 provider，恢复则 checkpoint 续跑原任务；超时落库 notice。
GRACE_WINDOW_SEC = 30
GRACE_PROBE_INTERVAL = 3


class RunManager:
    """管理进行中的 run（进程内单例，单用户本地应用）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, dict[str, bool]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._sessions: dict[str, str] = {}
        #: 运行中「通过消息引导发送」的引导消息队列（run 级，中间件每次模型调用前取用）。
        #: 项 = {"message_id": 落库消息 id, "content": 引导文本}；message_id 为 None 表示
        #: 系统内部纠正（不入树、不展示），用户引导在 API 层先落库（kind=steer）再入队。
        self._steer_queues: dict[str, list[dict[str, str | None]]] = {}
        #: run 级隐藏上下文（选区润色提示词等）——只注入模型请求，不落库不展示
        self._hidden_contexts: dict[str, str] = {}
        #: run 级用户消息原文与「写作意图」标记（读取授权守卫判定用）
        self._user_texts: dict[str, str] = {}
        self._read_intents: dict[str, bool] = {}
        #: 暂停标记：run 进入 awaiting_approval/awaiting_consult 时任务会结束，
        #: 但 run 并未终止——resume 会续跑同一 run。run 级上下文（用户消息/写作意图/
        #: 隐藏上下文/引导队列）必须跨暂停保留，否则续跑后读取豁免与润色提示词丢失。
        self._paused: dict[str, bool] = {}

    def set_paused(self, run_id: str, paused: bool) -> None:
        """finalize 收尾时登记暂停态；_cleanup 据此跳过 run 级上下文清理。"""
        if paused:
            self._paused[run_id] = True
        else:
            self._paused.pop(run_id, None)

    def running_ids(self) -> list[str]:
        return [rid for rid, t in self._tasks.items() if not t.done()]

    def live_run_id(self) -> str | None:
        """当前真正在跑的 run（任务未结束）；无则 None。全局单飞的判据。"""
        for rid, task in self._tasks.items():
            if not task.done():
                return rid
        return None

    def session_of(self, run_id: str) -> str | None:
        return self._sessions.get(run_id)

    # ---- 引导消息（steer） ----

    def push_steer(self, run_id: str, text: str, *, message_id: str | None = None) -> None:
        """运行中追加引导消息（SteerInjectionMiddleware 在下次模型调用前注入）。

        用户引导：API 层先落库（kind=steer）拿到 message_id 再入队；
        系统内部纠正（熔断提醒等）：message_id=None，注入时不落树不展示。
        """
        self._steer_queues.setdefault(run_id, []).append({"message_id": message_id, "content": text})

    def take_steer(self, run_id: str) -> list[dict[str, str | None]]:
        """取出并清空该 run 的引导消息（中间件调用）。"""
        return self._steer_queues.pop(run_id, [])

    def steer_pending(self, run_id: str) -> list[dict[str, str | None]]:
        """尚未被消费的引导消息（编辑/删除接口校验用）。"""
        return list(self._steer_queues.get(run_id, []))

    def update_steer(self, run_id: str, message_id: str, content: str) -> bool:
        """编辑未消费的引导消息；返回是否命中队列项。"""
        for item in self._steer_queues.get(run_id, []):
            if item.get("message_id") == message_id:
                item["content"] = content
                return True
        return False

    def remove_steer(self, run_id: str, message_id: str) -> bool:
        """删除未消费的引导消息；返回是否命中队列项。"""
        queue = self._steer_queues.get(run_id)
        if not queue:
            return False
        before = len(queue)
        self._steer_queues[run_id] = [i for i in queue if i.get("message_id") != message_id]
        return len(self._steer_queues[run_id]) != before

    # ---- run 级隐藏上下文（润色提示词等，不入消息树/不展示） ----

    def set_hidden_context(self, run_id: str, text: str) -> None:
        if text:
            self._hidden_contexts[run_id] = text

    def hidden_context_of(self, run_id: str) -> str:
        """读取该 run 的隐藏上下文（中间件每次模型调用读取；run 结束随 _cleanup 清除）。"""
        return self._hidden_contexts.get(run_id, "")

    # ---- run 级用户消息 / 写作意图（读取授权守卫用）----

    def note_user_message(self, run_id: str, text: str, hidden_context: str = "") -> None:
        """记录本 run 的用户消息原文与写作意图标记（start 时调用）。

        读取意图 = 写作意图命中 **或** 携带选区润色隐藏上下文（润色必然要读
        目标章节正文，不应逐次弹读取确认卡）。
        """
        self._user_texts[run_id] = text or ""
        try:
            from app.agent.workflows.writing_steps.intent import has_workflow_intent

            self._read_intents[run_id] = bool(has_workflow_intent(text or "")) or (
                "选中待润色文本" in (hidden_context or "")
            )
        except Exception as exc:  # pragma: no cover - 意图判定失败不阻断
            logger.warning("read_intent_detect_failed", run_id=run_id, error=str(exc))
            self._read_intents[run_id] = False

    def user_text_of(self, run_id: str) -> str:
        return self._user_texts.get(run_id, "")

    def read_intent_of(self, run_id: str) -> bool:
        return self._read_intents.get(run_id, False)

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
    ) -> str | None:
        """新建 run（落库 + 起后台任务），返回 run_id；返回 None 表示全局单飞拦截。

        **全局单飞**：已有其它会话的 run 在跑时，不再起新 run——把用户消息落树后
        直接以 `GLOBAL_BUSY_REPLY` 作 assistant 回复（不排队、不报错），
        对应「不允许同时有多个会话正在 AI 思考中」。

        `hidden_context`（如选区润色提示词）只注入模型请求，不入消息树、不展示。
        """
        async with session_scope() as db:
            agent_session = await agent_service.require_session(db, session_id)

            # 全局单飞：已有活 run（且不属于本会话）→ 直接 AI 回复提示，不起新 run
            live = self.live_run_id()
            if live is not None and self.session_of(live) != session_id:
                await add_message(
                    db, session_id=session_id, role="user", content=content,
                    parent_id=parent_id if parent_id else agent_session.active_leaf_id,
                )
                await add_message(
                    db, session_id=session_id, role="assistant", content=GLOBAL_BUSY_REPLY,
                )
                logger.info(
                    "run_rejected_global_busy",
                    session_id=session_id,
                    busy_run_id=live,
                    busy_session_id=self.session_of(live),
                )
                return None

            # 首条用户消息 → 会话标题自动改为「问题总结」（此前无 user 消息时才改）
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
            self.note_user_message(run_id, content, hidden_context=hidden_context)
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

    async def resume(self, *, run_id: str, resume_value: Any, interrupt_id: str | None = None) -> None:
        """中断恢复：用 `Command(resume=...)` 续跑同一 run（thread 不变，seq 续增）。

        `interrupt_id` = 业务中断行 id：多个中断同时挂起时 langgraph 要求 resume
        带「langgraph interrupt id → 值」映射，据此把值路由到对应中断。
        """
        async with session_scope() as db:
            run = await db.get(AgentRun, run_id)
            if run is None:
                raise ValueError(f"run 不存在：{run_id}")
            if run.status in ("completed", "failed", "cancelled"):
                raise run_state_conflict(run)
            run.status = "running"
            project_id, session_id = run.project_id, run.session_id
            model_ref = run.model_ref
        # 续跑开始：清除暂停标记（本轮收尾若再暂停会重新登记）
        self.set_paused(run_id, False)

        # 重开流：finished=False，seq 续增，缓冲保留（客户端可无缝续传）
        await sse_bus.reopen(run_id, session_id)
        self._spawn(
            run_id, project_id, session_id, None, model_ref, None,
            resume_value=resume_value, interrupt_id=interrupt_id,
        )

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
        # 主动停止 → 未消费引导收养进活跃路径 + 落一条 Agent 默认收尾回复
        await _adopt_pending_steers(session_id, run_id)
        await _append_notice(session_id, run_id, notice_text_for("cancelled"))
        await sse_bus.publish(run_id, session_id, "run.notice", {"content": notice_text_for("cancelled"), "kind": "cancelled"})
        # 取消同样释放「AI 编辑锁」。`_execute_run` 的 finally 也会释放，
        # 但取消可能发生在 run 尚未进入执行体时（此时不会有 finally），
        # 故这里是必要的兜底；重复释放幂等无害。
        try:
            from app.services.content_lock import content_locks

            freed = await content_locks.release_for_run(run_id)
            for cid, pid in freed:
                await sse_bus.publish(
                    run_id,
                    session_id,
                    "content.lock",
                    {"chapter_id": cid, "run_id": run_id, "locked": False, "project_id": pid},
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
        interrupt_id: str | None = None,
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
                interrupt_id=interrupt_id,
            ),
            name=f"agent-run-{run_id[:8]}",
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._cleanup(run_id))

    def _cleanup(self, run_id: str) -> None:
        # 暂停态（审批/咨询）任务结束但 run 未终止：只释放任务登记，
        # 保留 run 级上下文供 resume 续跑（用户消息/写作意图/隐藏上下文/引导）。
        if self._paused.get(run_id):
            self._tasks.pop(run_id, None)
            return
        self._tasks.pop(run_id, None)
        self._cancel_flags.pop(run_id, None)
        self._locks.pop(run_id, None)
        self._sessions.pop(run_id, None)
        self._hidden_contexts.pop(run_id, None)
        self._steer_queues.pop(run_id, None)
        self._user_texts.pop(run_id, None)
        self._read_intents.pop(run_id, None)
        self._paused.pop(run_id, None)
        # 第一层注入槽位登记随 run 释放（ContextGuard 第二层保险用）
        from app.agent.loop.middleware.recap import clear_injected_slots

        clear_injected_slots(run_id)

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
    interrupt_id: str | None = None,
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
            interrupt_id=interrupt_id,
            cancel_flag=cancel_flag,
        )
        return

    tokens_in = tokens_out = cached_in = 0
    recursion_used = 0
    final_status = "completed"
    error_text: str | None = None
    fallback_count = 0
    #: 宽限续跑可用的运行期对象（异常发生在装配前时为 None，跳过宽限）
    graph: Any = None
    config: dict[str, Any] | None = None
    context: AgentContext | None = None
    emitter: Emitter | None = None
    resolved_ref: str | None = None
    #: 本轮是否已落库 notice（run.end 携带 notice 标记，前端据此隐藏灰卡）
    run_notice = False

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
        context_window = emitter.max_context
        context = AgentContext(
            project_id=project_id,
            session_id=session_id,
            run_id=run_id,
            model_ref=resolved_ref or "",
            emit=emitter,
            cancel_flag=cancel_flag,
        )

        # ---- 请求级注入 provider（前情提要按需 + 目标章大纲摘录）----
        recap_provider = make_recap_provider(project_id, session_id)
        outline_ctx_provider = make_outline_ctx_provider(project_id)

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
                outline_ctx_provider=outline_ctx_provider,
                subagents_enabled=settings.agent_subagents_enabled,
                recursion_limit=settings.agent_recursion_limit,
                session_id=session_id,
                subagent_names=sub_names or None,
                skill_names=sk_names or None,
                # run 级隐藏上下文（润色提示词等）请求级注入，不落库不展示
                hidden_context_provider=run_manager.hidden_context_of,
                max_context=context_window,
            )
            graph = agent["graph"]

        config = graph_config(project_id, session_id, agent.get("recursion_limit", 60))
        attach_langfuse(config, project_id=project_id, session_id=session_id, run_id=run_id)

        # checkpoint 为空（进程重启 / 清理）时从业务库回填会话历史，
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

            stream_input = Command(
                resume=await resume_input_for(
                    run_id, interrupt_id, resume_value, graph=graph, config=config
                )
            )
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
        cached_in = result.cached_in
        recursion_used = result.recursion_used
        fallback_count = result.fallback_count

        if result.interrupted:
            final_status = await interrupt_status(run_id)
        elif result.cancelled:
            final_status = "cancelled"
        else:
          # 收尾守护——本轮有文件写入但模型未给最终文字回复（「没干完就收尾」），
            # 自动续跑一轮要求模型自检并给出最终答复；最多 1 次，仍无回复则如实告知。
            nudge = 0
            while nudge < 1 and final_status == "completed":
                if not await _needs_completion_nudge(graph, config, user_message_id):
                    break
                nudge += 1
                logger.warning("agent_run_missing_final_reply", run_id=run_id, nudge=nudge)
                result = await stream_graph(
                    graph=graph,
                    config=config,
                    context=context,
                    stream_input={
                        "messages": [
                            (
                                "user",
                                "（系统守护）你上一轮没有给出最终回复就收尾了。请检查待办是否全部完成："
                                "未完成则继续用工具完成；已全部完成则用一段中文简要总结本次改动；"
                                "若因异常无法完成，请说明原因。",
                            )
                        ]
                    },
                    run_id=run_id,
                    session_id=session_id,
                    emitter=emitter,
                    cancel_flag=cancel_flag,
                    syncer=MessageSyncer(session_id=session_id),
                    user_message_id=None,
                    resumed=False,
                )
                tokens_in += result.tokens_in
                tokens_out += result.tokens_out
                cached_in += result.cached_in
                recursion_used += result.recursion_used
                fallback_count += result.fallback_count
                if result.interrupted:
                    final_status = await interrupt_status(run_id)
                elif result.cancelled:
                    final_status = "cancelled"

    except asyncio.CancelledError:
        # ★ 规范化取消：asyncio.CancelledError 只可能是用户主动停止（cancel API 置 flag 后 task.cancel()）
        # 或进程退出。统一当「用户主动停止」处理，不抛异常，优雅收尾。
        final_status = "cancelled"
        error_text = None
        logger.info("agent_run_cancelled", run_id=run_id)
        await _adopt_pending_steers(session_id, run_id)
        await _append_notice(session_id, run_id, notice_text_for("cancelled"))
        await sse_bus.publish(run_id, session_id, "run.notice", {"content": notice_text_for("cancelled"), "kind": "cancelled"})
    except GraphRecursionError as exc:
      # 步数触顶不再当「服务端内部错误」——优雅收尾：
        # 落一条 assistant 说明消息 + 正常 completed，避免用户看到 50001 灰卡。
        final_status = "completed"
        error_text = None
        logger.warning("agent_run_recursion_limit_reached", run_id=run_id, error=str(exc)[:200])
        await _adopt_pending_steers(session_id, run_id)
        await _append_recursion_notice(session_id, run_id)
        await sse_bus.publish(
            run_id,
            session_id,
            "message.complete",
            {
                "message_id": f"recursion-{run_id}",
                "content": _RECURSION_NOTICE,
                "tool_calls": [],
            },
        )
    except Exception as exc:  # noqa: BLE001 - 运行期异常统一收敛
        final_status = "failed"
        error_code, error_text = error_of(exc)
        logger.error("agent_run_failed", run_id=run_id, code=error_code, error=error_text)
        await sse_bus.publish(run_id, session_id, "error", {"code": error_code, "message": error_text})
        # ---- 网络/模型接口异常：30s 宽限，每 3s 探测一次，恢复则 checkpoint 续跑 ----
        fkind = classify_failure(exc)
        if (
            fkind in ("network", "llm")
            and graph is not None
            and config is not None
            and not cancel_flag.get("cancelled")
        ):
            result2 = await _grace_resume(
                run_id=run_id,
                session_id=session_id,
                graph=graph,
                config=config,
                context=context,
                emitter=emitter,
                model_ref=resolved_ref or model_ref,
                cancel_flag=cancel_flag,
                first_kind=fkind,
            )
            if result2 is not None:
                tokens_in += result2.tokens_in
                tokens_out += result2.tokens_out
                cached_in += result2.cached_in
                recursion_used += result2.recursion_used
                fallback_count += result2.fallback_count
                if result2.interrupted:
                    final_status = await interrupt_status(run_id)
                elif result2.cancelled:
                    final_status = "cancelled"
                else:
                    final_status = "completed"
                error_text = None
            else:
                run_notice = True
                notice = notice_text_for(fkind, error_text)
                await _adopt_pending_steers(session_id, run_id)
                await _append_notice(session_id, run_id, notice)
                await sse_bus.publish(
                    run_id, session_id, "run.notice", {"content": notice, "kind": fkind}
                )
    finally:
        try:
            await finalize(
                run_id=run_id,
                session_id=session_id,
                project_id=project_id,
                status=final_status,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cached_in=cached_in,
                recursion_used=recursion_used,
                fallback_count=fallback_count,
                error=error_text,
            )
        except Exception as exc:  # pragma: no cover
            logger.error("run_finalize_failed", run_id=run_id, error=str(exc))

        await publish_run_end(
            run_id, session_id, final_status, tokens_in, tokens_out, error_text, notice=run_notice
        )
        await sse_bus.close(run_id)


#: 进程内单例
run_manager = RunManager()


async def _adopt_pending_steers(session_id: str, run_id: str) -> None:
    """run 收尾时把**未进入活跃路径**的引导消息收养进活跃路径。

    steer 行落库时不推进 active_leaf（兄弟节点）；若 run 在消费前停止/失败，
    这些行会游离在活跃路径之外（重进会话不可见）。收尾时按落库顺序把它们
    接到当前 active_leaf 之后并推进 leaf，保证「用户消息 + 引导 + 默认回复」完整可见。
    已消费的引导本就在活跃路径上（MessageSyncer 衔接），自动跳过。
    """
    try:
        async with session_scope() as db:
            sess = await db.get(AgentSession, session_id)
            if sess is None:
                return
            rows = (
                (
                    await db.execute(
                        select(Message)
                        .where(
                            Message.session_id == session_id,
                            Message.run_id == run_id,
                            Message.kind == "steer",
                        )
                        .order_by(Message.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return
            parent_of = dict(
                (
                    await db.execute(
                        select(Message.id, Message.parent_id).where(Message.session_id == session_id)
                    )
                ).all()
            )
            on_path: set[str] = set()
            cur = sess.active_leaf_id
            while cur:
                on_path.add(cur)
                cur = parent_of.get(cur)
            changed = False
            for row in rows:
                if row.id in on_path:
                    continue
                row.parent_id = sess.active_leaf_id
                row.branch_index = 0
                sess.active_leaf_id = row.id
                on_path.add(row.id)
                changed = True
            if changed:
                sess.updated_at = utcnow()
    except Exception as exc:  # pragma: no cover - 收养失败不阻断收尾
        logger.warning("steer_adopt_failed", run_id=run_id, error=str(exc))


async def _append_notice(session_id: str, run_id: str, content: str) -> None:
    """落一条 Agent 默认收尾回复（kind=notice）：暂停/网络异常/模型接口异常话术。

    落库即「重进会话/重启服务仍可见」；前端经 run.notice 事件即时展示，
    run.end 的 notice 标记用于隐藏灰色报错卡片（避免一条回复 + 灰卡双显示）。
    """
    try:
        async with session_scope() as db:
            await add_message(
                db,
                session_id=session_id,
                role="assistant",
                content=content,
                run_id=run_id,
                kind="notice",
            )
    except Exception as exc:  # pragma: no cover - 落库失败不阻断收尾
        logger.warning("run_notice_persist_failed", run_id=run_id, error=str(exc))


async def _grace_resume(
    *,
    run_id: str,
    session_id: str,
    graph: Any,
    config: dict[str, Any],
    context: AgentContext | None,
    emitter: Emitter | None,
    model_ref: str | None,
    cancel_flag: dict[str, bool],
    first_kind: str,
) -> StreamResult | None:
    """网络/模型接口异常后的宽限续跑：30s 内每 3s 探测 provider。

    - 探测恢复 → `stream_graph(stream_input=None)` 从 checkpoint 续跑原任务，返回结果；
    - 窗口耗尽 → 返回 None（调用方落库 notice）；
    - 续跑再次失败：网络类继续探测（共享同一窗口），模型接口类不再续跑（避免烧 token）。
    """
    if context is None or emitter is None:
        return None
    deadline = time.monotonic() + GRACE_WINDOW_SEC
    kind = first_kind
    while time.monotonic() < deadline:
        await asyncio.sleep(GRACE_PROBE_INTERVAL)
        if cancel_flag.get("cancelled"):
            return None
        async with session_scope() as db:
            reachable = await probe_provider(db, model_ref)
        if not reachable:
            continue
        logger.info("grace_resume_probing_ok", run_id=run_id, kind=kind)
        await sse_bus.publish(run_id, session_id, "run.resuming", {"kind": kind})
        try:
            return await stream_graph(
                graph=graph,
                config=config,
                context=context,
                stream_input=None,  # checkpoint 续跑：不追加新输入，从断点继续
                run_id=run_id,
                session_id=session_id,
                emitter=emitter,
                cancel_flag=cancel_flag,
                syncer=MessageSyncer(session_id=session_id),
                user_message_id=None,
                resumed=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc2:  # noqa: BLE001 - 续跑再失败：网络类继续探测
            kind = classify_failure(exc2)
            logger.warning("grace_resume_retry_failed", run_id=run_id, kind=kind, error=str(exc2)[:200])
            if kind != "network":
                return None
    return None

#: 步数触顶时的用户可见说明（落库 + SSE 双通道）
_RECURSION_NOTICE = (
    "本次任务步骤较多，已达到单轮执行的步数上限，我先停下来汇报当前进度。"
    "已完成的部分均已保存；请回复「继续」，我会从当前进度接着完成剩余工作。"
)


async def _needs_completion_nudge(graph: Any, config: dict[str, Any], user_message_id: str | None) -> bool:
    """判定本轮是否「没干完就收尾」。

    条件（同时满足）：
    - 本轮（user_message_id 之后）存在 `write_file` / `edit_file` 工具调用；
    - 最后一条 tool 消息之后**没有**带正文的 assistant 消息（模型没给最终回复）。
    读取 graph state 失败时保守返回 False（不误续跑）。
    """
    try:
        state = await graph.aget_state(config)
    except Exception as exc:  # pragma: no cover
        logger.debug("nudge_state_read_failed", error=str(exc))
        return False
    messages = list((state.values or {}).get("messages") or [])
    start = 0
    if user_message_id:
        for i, m in enumerate(messages):
            if getattr(m, "id", None) == user_message_id:
                start = i
                break
    turn = messages[start:]
    if not turn:
        return False
    has_write = False
    last_tool_idx = -1
    for i, m in enumerate(turn):
        mtype = getattr(m, "type", "")
        if mtype == "ai":
            for tc in getattr(m, "tool_calls", None) or []:
                if (tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")) in (
                    "write_file",
                    "edit_file",
                ):
                    has_write = True
        elif mtype == "tool":
            last_tool_idx = i
    if not has_write or last_tool_idx < 0:
        return False
    for m in turn[last_tool_idx + 1 :]:
        if getattr(m, "type", "") == "ai" and str(getattr(m, "content", "") or "").strip():
            return False
    return True


async def _append_recursion_notice(session_id: str, run_id: str) -> None:
    """步数触顶时落一条 assistant 说明消息（会话里可见，替代 50001 报错卡）。"""
    try:
        from app.agent.tracing.messages.tree import add_message
        from app.db.session import session_scope

        async with session_scope() as db:
            await add_message(
                db,
                session_id=session_id,
                role="assistant",
                content=_RECURSION_NOTICE,
                run_id=run_id,
            )
    except Exception as exc:  # pragma: no cover - 落库失败不阻断收尾
        logger.warning("recursion_notice_persist_failed", run_id=run_id, error=str(exc))


async def seed_history_if_empty(
    graph: Any, config: dict[str, Any], session_id: str, *, exclude_id: str | None = None
) -> None:
    """checkpoint 为空时从业务库回填会话历史（进程重启 / 清理后不「失忆」）。

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
