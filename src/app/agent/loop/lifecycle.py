# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.lifecycle
文件名: lifecycle.py
作者: xionggaoqing
时间: 2026-10-27
说明: run 生命周期公共件：状态标记、graph config、错误码归一、run.end 发布与收尾落库
    （手册「二、(2)」：收尾不在中间件里做，由 runner 统一执行）。

    普通运行（loop/runner）与门禁运行（gates/runner）共用，故独立成模块以免互相依赖。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop.events import sse_bus
from app.agent.memory import thread_id_of
from app.agent.tracing import create_score, current_trace_id
from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models import AgentRun, AgentSession
from app.db.session import session_scope
from app.services import agent as agent_service

logger = get_logger(__name__)


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
        # ★v1101：释放本 run 持有的「AI 编辑锁」并通知前端解锁（暂停态不释放——run 还可能续写）
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
