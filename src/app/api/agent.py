# -*- coding: utf-8 -*-
"""
模块: app.api.agent
文件名: agent.py
作者: xionggaoqing
时间: 2026-10-27
说明: Agent API：会话、消息、运行、SSE 流、取消、中断 resolve（手册「六 → 路由树 · Agent」）。

        GET|POST   /agent/sessions?novel_id=          会话列表/新建（按项目隔离）
        PATCH|DELETE /agent/sessions/{sid}
        GET /agent/sessions/{sid}/messages 消息树（含分支）
        POST /agent/sessions/{sid}/messages 发消息 → 202 + {run_id}
        POST /agent/sessions/{sid}/branch 切换 active_leaf_id（分支/重新生成）
        PATCH      /agent/sessions/{sid}/context      会话级上下文
        GET        /agent/runs/{run_id}               运行详情（状态/用量/流程）
        GET /agent/runs/{run_id}/stream SSE（run 级）
        POST /agent/runs/{run_id}/cancel 取消（协作式）
        POST /agent/interrupts/{id}/resolve 审批与咨询统一入口
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

# from app.agent.loop.runner import run_manager  # 延迟到端点函数内导入（~20s）

# agent 的**运行入口**：API 层直接持有 run_manager（起 run / 恢复 / 取消）与 sse_bus（订阅流）。
# 这两个是「运行 Agent」的驱动面，不是取数——数据访问（会话列表/消息树/工具日志/分支切换）
# 已全部下沉到 `services.agent`，故本模块不再直连 agent 的取数函数，
# 与其它 13 个 api 模块的取数路径保持一致（都经 services）。
# from app.agent.tracing.sse.events import buffer_lost_event, sse_bus  # 延迟到端点函数内导入
from app.core.deps import get_session
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.response import ok
from app.db.models import AgentRun
from app.db.session import session_scope
from app.schemas import (
    AgentBranchIn,
    AgentInterruptResolveIn,
    AgentMessageIn,
    AgentSessionContext,
    AgentSessionCreate,
    AgentSessionUpdate,
    AgentSteerIn,
)
from app.services import agent as agent_service

logger = get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


def _live_run_ids() -> list[str]:
    """当前进程内真正在跑的 run（任务未结束）。"""
    from app.agent.loop.runner import run_manager  # 延迟导入

    return [rid for rid in run_manager.running_ids() if run_manager.is_running(rid)]


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------


@router.get("/sessions")
async def get_sessions(
    novel_id: str | None = Query(default=None), session: AsyncSession = Depends(get_session)
) -> dict:
    """会话列表（按项目隔离：带 novel_id 只返回该项目会话）。

    每条会话附 `is_running`（进程内有活 run）——会话列表右侧据此
    灰色小字提示「AI思考执行中……」。
    """
    dtos = await agent_service.list_sessions(session, novel_id)
    from app.agent.loop.runner import run_manager  # 延迟导入

    running_sessions = {run_manager.session_of(rid) for rid in _live_run_ids()}
    for dto in dtos:
        dto["is_running"] = dto["id"] in running_sessions
    return ok(dtos)


@router.get("/usage")
async def get_usage(
    novel_id: str = Query(...), session: AsyncSession = Depends(get_session)
) -> dict:
    """项目用量。"""
    return ok(await agent_service.project_usage(session, novel_id))


@router.get("/sessions/{session_id}/usage")
async def get_session_usage(session_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """会话上下文占用。

    返回最近一次 run 的输入 token——重新进入历史会话时用它还原占用，
    否则旧会话会被错误显示为 100% 剩余。
    """
    return ok(await agent_service.session_usage(session, session_id))


@router.post("/sessions")
async def create_session(body: AgentSessionCreate, session: AsyncSession = Depends(get_session)) -> dict:
    dto = await agent_service.create_session(
        session, project_id=body.novel_id, title=body.title, model_ref=body.model_ref
    )
    await session.commit()
    return ok(dto)


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str, body: AgentSessionUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    dto = await agent_service.rename_session(session, session_id, title=body.title or "")
    await session.commit()
    return ok(dto)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    dto = await agent_service.delete_session(session, session_id)
    await session.commit()
    return ok(dto)


@router.patch("/sessions/{session_id}/context")
async def update_context(
    session_id: str, body: AgentSessionContext, session: AsyncSession = Depends(get_session)
) -> dict:
    """会话级上下文（模型/子代理/技能/上下文章节）。"""
    dto = await agent_service.update_context(
        session,
        session_id,
        model_ref=body.model_ref,
        subagent_ids=body.subagent_ids,
        skill_ids=body.skill_ids,
        context_chapter_ids=body.context_chapter_ids,
    )
    await session.commit()
    return ok(dto)


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """消息树（含分支与 active 路径标记）。"""
    return ok(await agent_service.load_message_tree(session, session_id))


@router.post("/sessions/{session_id}/messages", status_code=202)
async def post_message(
    request: Request, session_id: str, body: AgentMessageIn, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    """发消息 → **202** + `{run_id}`；事件走 `GET /agent/runs/{run_id}/stream`。

    先建流再发消息、或先发消息再建流都支持（环形缓冲补齐）。
    **全局单飞**：其它会话正在思考时 `run_id` 为 null 且带 `rejected="global_busy"`——
    用户消息与 AI 的提示回复已落消息树，前端刷新消息即可看到。
    """
    row = await agent_service.require_session(session, session_id)
    if row.status == "running" and _has_live_run(row.id):
        return JSONResponse(
            status_code=409,
            content={
                "code": 40902,
                "message": "该会话有正在运行的任务，请等待完成或先取消",
                "data": {"session_id": session_id},
            },
        )

    # 流程（四阶段工作流）改为意图驱动——无「生成本章」按钮；
    # 用户消息表达「按大纲某章简介生成/修改正文」时才把 run 标记为固定流程运行。
    target_chapter_id = body.target_chapter_id or ""
    if not target_chapter_id:
        target_chapter_id = await _detect_workflow_target(row.project_id, body)

    from app.agent.loop.runner import GLOBAL_BUSY_REPLY, run_manager  # 延迟导入

    run_id = await run_manager.start(
        project_id=row.project_id,
        session_id=session_id,
        content=body.content,
        model_ref=body.model_ref or row.model_ref,
        parent_id=body.parent_id,
        target_chapter_id=target_chapter_id,
        hidden_context=_pointerize_attachments(row.project_id, body.hidden_context or ""),
    )
    # 全局单飞：其它会话正在思考 → 不起 run，AI 已直接回复提示文案（消息树可见）
    if run_id is None:
        await session.commit()
        return JSONResponse(
            status_code=202,
            content={
                "code": 0,
                "message": "accepted",
                "data": {
                    "run_id": None,
                    "session_id": session_id,
                    "rejected": "global_busy",
                    "reply": GLOBAL_BUSY_REPLY,
                    "workflow_target_chapter_id": None,
                },
            },
        )
    await session.commit()
    return JSONResponse(
        status_code=202,
        content={
            "code": 0,
            "message": "accepted",
            "data": {
                "run_id": run_id,
                "session_id": session_id,
                # 前端据此展示流程状态条（意图命中才非空）
                "workflow_target_chapter_id": target_chapter_id or None,
            },
        },
    )


def _pointerize_attachments(project_id: str, hidden_context: str) -> str:
    """添加文件全文落盘，hidden_context 只留指针（选区润色块保持内联）。"""
    if not (hidden_context or "").strip():
        return hidden_context or ""
    from app.db.base import new_id
    from app.services.attachments import prune_attachments, rewrite_hidden_context

    #: 批次标记（uuid，无下划线 → prune 按 `{tag}_{n}` 稳定分组）
    tag = new_id()
    try:
        rewritten = rewrite_hidden_context(project_id, tag, hidden_context)
    except Exception as exc:  # noqa: BLE001 - 指针化失败不阻断发送（退回全文内联）
        logger.warning("attachments_pointerize_failed", project_id=project_id, error=str(exc))
        return hidden_context
    try:
        prune_attachments(project_id)
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响本次发送
        logger.warning("attachments_prune_failed", project_id=project_id, error=str(exc))
    return rewritten


async def _detect_workflow_target(project_id: str, body: AgentMessageIn) -> str:
    """意图检测：命中「按大纲生成/修改正文」→ 返回目标章 id；否则空串（普通对话）。"""
    from app.agent.loop.knowledge.chapters import list_chapters_ordered
    from app.agent.workflows.writing_steps.intent import detect_workflow_target

    try:
        chapters = await list_chapters_ordered(project_id)
    except Exception as exc:  # noqa: BLE001 - 检测失败不阻断普通对话
        logger.warning("workflow_intent_chapters_failed", error=str(exc))
        return ""
    return detect_workflow_target(body.content, chapters, body.active_chapter_id) or ""


def _has_live_run(session_id: str) -> bool:
    from app.agent.loop.runner import run_manager  # 延迟导入

    return any(run_manager.session_of(rid) == session_id for rid in _live_run_ids())


@router.post("/sessions/{session_id}/branch")
async def switch_branch(
    session_id: str, body: AgentBranchIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """切换 active_leaf_id（分支查看 / 重新生成前的回退）。"""
    dto = await agent_service.switch_branch(session, session_id, body.message_id)
    await session.commit()
    return ok(dto)


@router.get("/sessions/{session_id}/tool-calls")
async def get_tool_calls(
    session_id: str, limit: int = Query(default=100, ge=1, le=500), session: AsyncSession = Depends(get_session)
) -> dict:
    """工具调用日志（运行日志面板）。"""
    return ok(await agent_service.list_tool_calls(session, session_id, limit=limit))


# ---------------------------------------------------------------------------
# 运行 / SSE
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}")
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """运行详情（状态/用量/流程/中断列表）。"""
    return ok(await agent_service.run_dto(session, run_id))


@router.get("/runs/{run_id}/stream")
async def stream_run(
    request: Request,
    run_id: str,
    last_event_id: int | None = Query(default=None, alias="last_event_id"),
) -> Any:
    """SSE 流（run 级；run_id 全局唯一即可定位 session）。

    - 信封 `{event, run_id, session_id, seq, ts, data}`，`seq` 每 run 内单调递增；
    - 断线续传位点：`Last-Event-ID` 请求头（EventSource 自动重连）或
      `?last_event_id={seq}`（手动重连；EventSource 无法自定义 header）；
    - 缓冲丢失 → error(41001) 并关流；run 已结束时补发缓冲后正常关流。
    """
    from app.agent.tracing.sse.events import buffer_lost_event, sse_bus  # 延迟导入

    async with _db() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            raise NotFoundError(f"run 不存在：{run_id}")
        session_id = run.session_id

    header_seq = request.headers.get("last-event-id")
    if header_seq:
        try:
            header_value = int(header_seq)
            last_event_id = header_value if last_event_id is None else max(last_event_id, header_value)
        except ValueError:
            pass
    last_seq = last_event_id

    queue, backlog, lost, finished = await sse_bus.subscribe(run_id, last_event_id=last_seq)

    async def event_gen() -> AsyncIterator[dict[str, Any]]:
        try:
            if lost:
                evt = buffer_lost_event(run_id, session_id)
                yield {"event": evt.event, "id": str(evt.seq), "data": evt.to_json()}
                return

            for evt in backlog:
                yield {"event": evt.event, "id": str(evt.seq), "data": evt.to_json()}
                if evt.event in ("run.end", "error"):
                    return

            if finished:
                return

            while True:
                if await request.is_disconnected():
                    return
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    # 心跳保活（注释行，不占 seq）
                    yield {"comment": "ping"}
                    continue
                if item is None:  # 关闭哨兵
                    return
                yield {"event": item.event, "id": str(item.seq), "data": item.to_json()}
                if item.event in ("run.end", "error"):
                    return
        finally:
            from app.agent.tracing.sse.events import sse_bus  # 延迟导入

            await sse_bus.unsubscribe(run_id, queue)

    return EventSourceResponse(event_gen(), ping=0)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """取消运行（协作式：置取消标志 → 当前工具/模型调用返回后停）。"""
    from app.agent.loop.runner import run_manager  # 延迟导入

    run = await session.get(AgentRun, run_id)
    if run is None:
        raise NotFoundError(f"run 不存在：{run_id}")
    dto = await run_manager.cancel(run_id)
    await session.commit()
    return ok(dto)


@router.post("/runs/{run_id}/steer")
async def steer_run(
    run_id: str, body: AgentSteerIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """运行中「通过消息引导发送」。

    引导文本入 run 级队列，由 `SteerInjectionMiddleware` 在**下次模型调用前**注入，
    不中断当前 run、不新建 run。run 已结束 → 409/40902（前端回落为普通发送）。
    """
    from app.agent.loop.runner import run_manager  # 延迟导入

    run = await session.get(AgentRun, run_id)
    if run is None:
        raise NotFoundError(f"run 不存在：{run_id}")
    if not run_manager.is_running(run_id):
        from app.core.errors import ApiError, ErrorCode

        raise ApiError(
            f"运行已结束（{run.status}），无法引导发送",
            code=ErrorCode.RUN_STATE_CONFLICT,
            data={"run_id": run_id, "status": run.status},
        )
    run_manager.push_steer(run_id, body.content)
    logger.info("run_steered", run_id=run_id, chars=len(body.content))
    return ok({"run_id": run_id, "steered": True})


# ---------------------------------------------------------------------------
# 中断 resolve
# ---------------------------------------------------------------------------


@router.post("/interrupts/{interrupt_id}/resolve")
async def resolve_interrupt(
    interrupt_id: str, body: AgentInterruptResolveIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """审批与咨询统一入口。成功后异步驱动 run 续跑。

    - approval: decision=approve|edit|reject|respond（edit 带 edited_args，respond 带 message）
    - consult:  decision=option|text（option 带 choice，text 带 text）
    """
    result = await agent_service.resolve_interrupt(
        session,
        interrupt_id,
        decision=body.decision,
        edited_args=body.edited_args,
        message=body.message,
        choice=body.choice,
        text=body.text,
    )
    await session.commit()

    # 异步续跑（不阻塞响应；失败会落 run.error 并发 error 事件）
    from app.agent.loop.runner import run_manager  # 延迟导入

    asyncio.get_running_loop().create_task(
        run_manager.resume(
            run_id=result["run_id"],
            resume_value=result["resume_value"],
            interrupt_id=interrupt_id,
        ),
        name=f"agent-resume-{result['run_id'][:8]}",
    )
    return ok(
        {
            "interrupt_id": interrupt_id,
            "run_id": result["run_id"],
            "decision": body.decision,
            "resumed": True,
        }
    )


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _db():

    return session_scope()
