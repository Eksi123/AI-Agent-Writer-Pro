# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.lifecycle
文件名: lifecycle.py
作者: xionggaoqing
时间: 2026-10-27
说明: run 生命周期公共件：状态标记、graph config、错误码归一、run.end 发布与收尾落库
    （手册「二、(2)」：收尾不在中间件里做，由 runner 统一执行）。

    普通运行（loop/runner）与固定流程运行（chapter_workflow/runner）共用，故独立成模块以免互相依赖。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.more.langfuse import create_score, current_trace_id
from app.agent.tracing.messages.tree import thread_id_of
from app.agent.tracing.sse.events import sse_bus
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models import AgentInterrupt, AgentRun, AgentSession
from app.db.session import session_scope
from app.services import agent as agent_service

logger = get_logger(__name__)

#: 「决定对不上任何挂起中断」时使用的占位键（合法 xxh3 格式，不会命中真实命名空间）。
#: 目的：让 `Command(resume=...)` 仍被 langgraph 认作 **id 映射**而非全局标量，
#: 从而不投递给任何任务——宁可让用户再点一次，也绝不让一个批准流向另一个高危操作。
_UNMATCHED_RESUME_KEY = "0" * 32


def error_of(exc: Exception) -> tuple[int, str]:
    """ApiError 保留业务错误码（如 LlmProviderError → 50201），其余归 50001。"""

    if isinstance(exc, ApiError):
        return exc.code, exc.message
    return ErrorCode.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"


async def publish_run_end(
    run_id: str,
    session_id: str,
    final_status: str,
    tokens_in: int,
    tokens_out: int,
    error_text: str | None,
    notice: bool = False,
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
                # 已落库 Agent 默认收尾回复（暂停/网络/模型异常）→ 前端隐藏灰色报错卡片
                "notice": notice,
            },
        )


async def resume_input_for(
    run_id: str,
    interrupt_id: str | None,
    resume_value: Any,
    *,
    graph: Any = None,
    config: Any = None,
) -> Any:
    """构造 `Command(resume=...)` 的值（普通运行与固定流程运行共用）。

    langgraph 规则：**checkpoint 里多个中断同时挂起**时 resume 必须是
    「langgraph interrupt id（任务命名空间哈希）→ 值」的映射；checkpoint 里只有一个
    中断时标量也可用——但**本项目一律用映射**（★单个挂起时若发标量，langgraph 会记成
    全局 null_resume；此后任何任务调用 `interrupt()` 都会把它当成自己的答复，
    导致下一个高危写入未经用户批准就执行）。

    映射内容 = 每个挂起中断各自的决定：
    - 本次刚决的中断（`interrupt_id`）→ `resume_value`；
    - 兄弟中断若**已决**（用户先前批过、但值未送达而仍挂起）→ 用其落库的 response
      一并送达，使其重放时直接执行/返回，避免 run 永久停在 awaiting_*；
    - 兄弟中断若**未决**（用户还没答）→ 不放入映射，保持挂起，run 再次暂停并展示卡片。

    中断 id 定位：行 payload 的 `langgraph_interrupt_id`（流事件补记），缺失时按
    `tool_call_id` / `consult_kind` 在 checkpoint 快照里匹配。
    """
    row_lg: str | None = None
    match_key = ""
    if interrupt_id:
        async with session_scope() as db:
            row = await db.get(AgentInterrupt, interrupt_id)
            if row is not None:
                try:
                    payload = json.loads(row.payload_json or "{}")
                except ValueError:
                    payload = {}
                row_lg = str(payload.get("langgraph_interrupt_id") or "") or None
                match_key = str(payload.get("tool_call_id") or payload.get("consult_kind") or "")

    if graph is None or config is None:
        return resume_value
    try:
        snap = await graph.aget_state(config)
    except Exception as exc:  # pragma: no cover - 快照读取失败回落标量
        logger.warning("resume_state_read_failed", run_id=run_id, error=str(exc))
        return resume_value

    pending: list[tuple[str, str]] = []
    for task in snap.tasks or []:
        for intr in getattr(task, "interrupts", ()) or ():
            value = intr.value if isinstance(intr.value, dict) else {}
            key = str(value.get("tool_call_id") or value.get("consult_kind") or "")
            pending.append((str(intr.id), key))
    if not pending:
        # checkpoint 里没有挂起中断（重放时已由「已决行」直接执行）→ 标量即可
        return resume_value

    # 已决兄弟中断的落库决定（key → response），用于补齐映射
    decided_by_key: dict[str, Any] = {}
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(AgentInterrupt).where(
                    AgentInterrupt.run_id == run_id, AgentInterrupt.decision.is_not(None)
                )
            )
        ).scalars().all()
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
            response = json.loads(row.response_json or "{}")
        except ValueError:
            continue
        key = str(payload.get("tool_call_id") or payload.get("consult_kind") or "")
        if key:
            decided_by_key[key] = response

    entries: dict[str, Any] = {}
    for pid, key in pending:
        if (row_lg and pid == row_lg) or (match_key and key == match_key):
            entries[pid] = resume_value
        elif key in decided_by_key:
            entries[pid] = decided_by_key[key]
        # 未决兄弟：不放入映射 → 保持挂起
    if not entries:
        # 本次决定对不上任何挂起中断。常见成因：并行工具调用下某个任务的 `interrupt()`
        # 被取消（审批卡已建、中断未落 checkpoint），用户批准的正是这张「幽灵卡」。
        # 此时若退回标量或「只有一个挂起就给它」，该决定会被**另一个**高危写入当成
        # 自己的答复直接执行（未经批准就删章）——故按下面的判据决定失败关闭与否。
        undecided_keys = await _undecided_keys(run_id)
        pending_keys = {key for _pid, key in pending if key}
        if pending_keys & undecided_keys:
            # ★失败关闭：真正待批的卡还在（另一张），绝不把这个决定投递给它；
            # 返回匹配不到任何命名空间的 id 键映射 → langgraph 忽略 → 任务重放时再次
            # `interrupt()` 挂起 → 用户仍会看到那张真正待批的卡。
            logger.warning(
                "resume_target_unmatched",
                run_id=run_id,
                interrupt_id=interrupt_id,
                match_key=match_key,
                pending=len(pending),
            )
            return {_UNMATCHED_RESUME_KEY: resume_value}
        if len(pending) == 1:
            # 挂起中断在库里没有对应的未决卡（无从区分身份）→ 定向投递给唯一挂起者，
            # 避免 run 永久停在 awaiting_*（无卡可点）。
            entries[pending[0][0]] = resume_value
            return entries
        return resume_value
    return entries


async def _undecided_keys(run_id: str) -> set[str]:
    """本 run 内**尚未决定**的中断的匹配键（tool_call_id / consult_kind）集合。"""
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(AgentInterrupt).where(
                    AgentInterrupt.run_id == run_id, AgentInterrupt.decision.is_(None)
                )
            )
        ).scalars().all()
    keys: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except ValueError:
            continue
        key = str(payload.get("tool_call_id") or payload.get("consult_kind") or "")
        if key:
            keys.add(key)
    return keys


def graph_config(project_id: str, session_id: str, recursion_limit: int) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": thread_id_of(project_id, session_id)},
        "recursion_limit": recursion_limit,
    }


async def mark_running(db: AsyncSession, run_id: str) -> None:
    run = await db.get(AgentRun, run_id)
    if run is not None and run.status in ("pending", "cancelling"):
        run.status = "running"


async def finalize(
    *,
    run_id: str,
    session_id: str,
    project_id: str,
    status: str,
    tokens_in: int,
    tokens_out: int,
    cached_in: int = 0,
    recursion_used: int,
    fallback_count: int,
    error: str | None,
) -> None:
    """运行收尾：写 `agent_runs` 汇总 + 会话状态 + Langfuse score + 派生索引。

    手册「二、(2)」：收尾不在中间件里做（无 on_run_end 钩子），由 runner 统一执行。
    """

    paused = status in ("awaiting_approval", "awaiting_consult")

    async with session_scope() as db:
        run = await db.get(AgentRun, run_id)
        if run is not None:
            run.status = status
            run.tokens_in = tokens_in
            run.tokens_out = tokens_out
            run.cached_in = cached_in
            run.recursion_used = recursion_used
            run.fallback_count = fallback_count
            run.error = error
            if not paused:
                run.ended_at = utcnow()
            trace_id = current_trace_id()
            if trace_id:
                run.langfuse_trace_id = trace_id

        # run 进入终端态时，清理未决中断（孤儿）——否则前端每次进入会话
        # 都会显示审批卡片，用户点批准后收到"运行已结束"错误，形成死循环。
        if not paused and status in ("completed", "failed", "cancelled"):
            from app.db.models import AgentInterrupt

            orphan_interrupts = (
                await db.execute(
                    select(AgentInterrupt)
                    .where(AgentInterrupt.run_id == run_id, AgentInterrupt.decision.is_(None))
                )
            ).scalars().all()
            for intr in orphan_interrupts:
                intr.decision = "cancelled"
                intr.response_json = '{"note":"run_ended"}'
                intr.decided_at = utcnow()

        if not paused:
            sess = await db.get(AgentSession, session_id)
            if sess is not None:
                sess.status = "error" if status == "failed" else "idle"
                sess.updated_at = utcnow()

    if not paused:
        # 释放本 run 持有的「AI 编辑锁」并通知前端解锁（暂停态不释放——run 还可能续写）
        try:
            from app.services.content_lock import content_locks

            freed = await content_locks.release_for_run(run_id)
            for cid in freed:
                await sse_bus.publish(
                    run_id, session_id, "content.lock", {"chapter_id": cid, "run_id": run_id, "locked": False}
                )
        except Exception as exc:  # pragma: no cover
            logger.warning("content_lock_release_failed", run_id=run_id, error=str(exc))

        # 字数统计刷新（Agent 可能写了正文）
        try:
            async with session_scope() as db:
                from app.services import novels as novels_service

                await novels_service.refresh_project_index(db, project_id)
        except Exception as exc:  # pragma: no cover
            logger.debug("project_index_refresh_failed", error=str(exc))

        try:
            trace_id = current_trace_id()
            word_count = await agent_service.project_word_count(project_id)
            if trace_id:
                create_score("run_tokens", tokens_in + tokens_out, trace_id=trace_id)
                create_score("project_words", word_count, trace_id=trace_id)
                create_score("fallback_used", 1 if fallback_count else 0, trace_id=trace_id)
        except Exception as exc:  # pragma: no cover
            logger.debug("langfuse_score_skipped", error=str(exc))


def run_state_conflict(run: AgentRun) -> Any:

    return ApiError(
        f"运行已结束（{run.status}），无法继续操作",
        code=ErrorCode.RUN_STATE_CONFLICT,
        data={"run_id": run.id, "status": run.status},
    )
