#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.services.agent
文件名: agent.py
作者: xionggaoqing
时间: 2026-10-27
说明: Agent 服务层：会话 CRUD、消息树、中断 resolve（手册「三、(1) services」）。

    运行编排（astream/SSE）在 `agent/loop/runner.py`；本模块负责会话与中断的业务规则：
    - 会话严格按项目隔离（`project_id`）；
    - resolve 统一入口：approval（approve/edit/reject/respond）与 consult（option/text）；
    - 对已结束 run 的 resolve → 409/40902。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode, NotFoundError
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models import AgentInterrupt, AgentRun, AgentSession, Novel
from app.services import audit

logger = get_logger(__name__)

#: run 终态（不可再 resolve / cancel）
_TERMINAL_RUN_STATUS = ("completed", "failed", "cancelled")

#: approval 决策 → langchain HITL batched resume 形状
_APPROVAL_DECISIONS = ("approve", "edit", "reject", "respond")
#: consult 决策
_CONSULT_DECISIONS = ("option", "text")


# ---------------------------------------------------------------------------
# 会话 CRUD
# ---------------------------------------------------------------------------


async def create_session(
    session: AsyncSession,
    *,
    project_id: str,
    title: str | None = None,
    model_ref: str | None = None,
) -> dict[str, Any]:
    novel = await session.get(Novel, project_id)
    if novel is None or novel.status == "deleted":
        raise NotFoundError(f"项目不存在：{project_id}")

    # 统计该项目现有会话数，用于默认标题
    count = (
        await session.execute(select(AgentSession).where(AgentSession.project_id == project_id))
    ).scalars().all()

    row = AgentSession(
        id=new_id(),
        project_id=project_id,
        title=(title or "").strip() or f"会话 {len(count) + 1}",
        model_ref=model_ref,
        status="idle",
    )
    session.add(row)
    await session.flush()

    await audit.record(
        session,
        "agent.session.create",
        entity_type="agent_session",
        entity_id=row.id,
        detail={"project_id": project_id, "title": row.title},
    )
    return _session_dto(row, project_title=novel.title)


async def rename_session(session: AsyncSession, session_id: str, *, title: str) -> dict[str, Any]:
    row = await _require_session(session, session_id)
    new_title = (title or "").strip()
    if not new_title:
        from app.core.errors import BadRequestError

        raise BadRequestError("会话标题不能为空")
    row.title = new_title
    row.updated_at = utcnow()
    await session.flush()
    return _session_dto(row)


async def delete_session(session: AsyncSession, session_id: str) -> dict[str, Any]:
    row = await _require_session(session, session_id)
    if row.status == "running":
        raise ApiError("会话正在运行中，请先取消运行再删除", code=ErrorCode.RUN_STATE_CONFLICT)
    project_id = row.project_id
    await session.delete(row)
    await session.flush()
    await audit.record(
        session, "agent.session.delete", entity_type="agent_session", entity_id=session_id,
        detail={"project_id": project_id},
    )
    return {"id": session_id, "deleted": True}


async def update_context(
    session: AsyncSession,
    session_id: str,
    *,
    model_ref: str | None = None,
    subagent_ids: list[str] | None = None,
    skill_ids: list[str] | None = None,
    context_chapter_ids: list[str] | None = None,
) -> dict[str, Any]:
    row = await _require_session(session, session_id)
    changes: dict[str, Any] = {}

    if model_ref is not None and model_ref != row.model_ref:
        changes["model_ref"] = {"from": row.model_ref, "to": model_ref}
        row.model_ref = model_ref or None
    if subagent_ids is not None:
        new_json = json.dumps(subagent_ids, ensure_ascii=False)
        if new_json != row.subagent_ids_json:
            changes["subagent_ids"] = subagent_ids
            row.subagent_ids_json = new_json
    if skill_ids is not None:
        new_json = json.dumps(skill_ids, ensure_ascii=False)
        if new_json != row.skill_ids_json:
            changes["skill_ids"] = skill_ids
            row.skill_ids_json = new_json
    if context_chapter_ids is not None:
        new_json = json.dumps(context_chapter_ids, ensure_ascii=False)
        if new_json != row.context_chapter_ids_json:
            changes["context_chapter_ids"] = context_chapter_ids
            row.context_chapter_ids_json = new_json

    if changes:
        row.updated_at = utcnow()
        await session.flush()
        await audit.record(
            session, "agent.session.context", entity_type="agent_session", entity_id=session_id, detail=changes
        )
    return _session_dto(row)


def _session_dto(row: AgentSession, *, project_title: str = "") -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "project_title": project_title,
        "title": row.title,
        "model_ref": row.model_ref,
        "status": row.status,
        "active_leaf_id": row.active_leaf_id,
        "subagent_ids": _loads_list(row.subagent_ids_json),
        "skill_ids": _loads_list(row.skill_ids_json),
        "context_chapter_ids": _loads_list(row.context_chapter_ids_json),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _loads_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------


async def run_dto(session: AsyncSession, run_id: str) -> dict[str, Any]:
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise NotFoundError(f"run 不存在：{run_id}")

    interrupts = (
        (
            await session.execute(
                select(AgentInterrupt).where(AgentInterrupt.run_id == run_id).order_by(AgentInterrupt.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    gate_result: Any = None
    if run.gate_result_json:
        try:
            gate_result = json.loads(run.gate_result_json)
        except ValueError:
            gate_result = run.gate_result_json

    return {
        "id": run.id,
        "session_id": run.session_id,
        "project_id": run.project_id,
        "status": run.status,
        "model_ref": run.model_ref,
        "recursion_used": run.recursion_used,
        "tokens_in": run.tokens_in,
        "tokens_out": run.tokens_out,
        "fallback_count": run.fallback_count,
        "langfuse_trace_id": run.langfuse_trace_id,
        "error": run.error,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "gate_stage": run.gate_stage,
        "gate_retry_count": run.gate_retry_count,
        "gate_result": gate_result,
        "target_chapter_id": run.target_chapter_id,
        "interrupts": [_interrupt_dto(i) for i in interrupts],
    }


async def pending_interrupt(session: AsyncSession, run_id: str) -> AgentInterrupt | None:
    return (
        await session.execute(
            select(AgentInterrupt)
            .where(AgentInterrupt.run_id == run_id, AgentInterrupt.decision.is_(None))
            .order_by(AgentInterrupt.created_at.desc())
        )
    ).scalars().first()


def _interrupt_dto(row: AgentInterrupt) -> dict[str, Any]:
    payload: Any = None
    if row.payload_json:
        try:
            payload = json.loads(row.payload_json)
        except ValueError:
            payload = row.payload_json
    response: Any = None
    if row.response_json:
        try:
            response = json.loads(row.response_json)
        except ValueError:
            response = row.response_json
    return {
        "id": row.id,
        "run_id": row.run_id,
        "session_id": row.session_id,
        "type": row.type,
        "tool_name": row.tool_name,
        "payload": payload,
        "decision": row.decision,
        "response": response,
        "decided_by": row.decided_by,
        "decided_at": row.decided_at,
        "created_at": row.created_at,
    }


# ---------------------------------------------------------------------------
# 中断 resolve（审批与咨询统一入口）
# ---------------------------------------------------------------------------


async def resolve_interrupt(
    session: AsyncSession,
    interrupt_id: str,
    *,
    decision: str,
    edited_args: dict[str, Any] | None = None,
    message: str | None = None,
    choice: str | None = None,
    text: str | None = None,
) -> dict[str, Any]:
    """审批与咨询统一入口（手册「六 → 路由树 · Agent」）。

    - approval: decision=approve|edit|reject|respond（edit 带 edited_args，respond 带 message）
    - consult:  decision=option|text（option 带 choice，text 带 text）

    返回 `{run_id, resume_value}`：调用方（API 层）据此驱动 runner.resume。
    """
    row = await session.get(AgentInterrupt, interrupt_id)
    if row is None:
        raise NotFoundError(f"中断不存在：{interrupt_id}")
    if row.decision is not None:
        raise ApiError(
            f"该中断已处理（{row.decision}）",
            code=ErrorCode.RUN_STATE_CONFLICT,
            data={"interrupt_id": interrupt_id, "decision": row.decision},
        )

    run = await session.get(AgentRun, row.run_id)
    if run is None:
        raise NotFoundError(f"run 不存在：{row.run_id}")
    if run.status in _TERMINAL_RUN_STATUS:
        raise ApiError(
            f"运行已结束（{run.status}），无法处理中断",
            code=ErrorCode.RUN_STATE_CONFLICT,
            data={"run_id": run.id, "status": run.status},
        )

    if row.type == "approval":
        if decision not in _APPROVAL_DECISIONS:
            from app.core.errors import BadRequestError

            raise BadRequestError(f"审批决策必须是 {_APPROVAL_DECISIONS} 之一，收到：{decision}")
        if decision == "edit" and not edited_args:
            from app.core.errors import BadRequestError

            raise BadRequestError("edit 决策必须提供 edited_args")
        resume_value = _approval_resume(
            decision,
            tool_name=_interrupt_tool_name(row),
            edited_args=edited_args,
            message=message,
        )
    elif row.type == "consult":
        if decision not in _CONSULT_DECISIONS:
            from app.core.errors import BadRequestError

            raise BadRequestError(f"咨询决策必须是 {_CONSULT_DECISIONS} 之一，收到：{decision}")
        resume_value = {"decision": decision, "choice": choice, "text": text or message}
    else:  # pragma: no cover - 防御
        from app.core.errors import BadRequestError

        raise BadRequestError(f"未知中断类型：{row.type}")

    # 落库决定
    response_record = {
        "decision": decision,
        "edited_args": edited_args,
        "message": message,
        "choice": choice,
        "text": text,
    }
    row.decision = decision
    row.response_json = json.dumps(
        {k: v for k, v in response_record.items() if v is not None}, ensure_ascii=False, default=str
    )
    row.decided_by = "user"
    row.decided_at = utcnow()

    run.status = "running"
    sess = await session.get(AgentSession, row.session_id)
    if sess is not None:
        sess.status = "running"
        sess.updated_at = utcnow()

    await session.flush()
    await audit.record(
        session,
        f"agent.interrupt.{row.type}",
        entity_type="agent_interrupt",
        entity_id=interrupt_id,
        detail={
            "run_id": row.run_id,
            "type": row.type,
            "tool_name": row.tool_name,
            "decision": decision,
            "edited_args": edited_args,
            "choice": choice,
            "message_excerpt": (message or text or "")[:200],
        },
    )

    return {"run_id": row.run_id, "session_id": row.session_id, "resume_value": resume_value}


def _interrupt_tool_name(row: AgentInterrupt) -> str:
    """从中断行取工具名（edit 决策的 `edited_action.name` 必须是真实工具名）。"""
    if row.tool_name:
        return row.tool_name
    payload: Any = None
    if row.payload_json:
        try:
            payload = json.loads(row.payload_json)
        except ValueError:
            payload = None
    if isinstance(payload, dict):
        requests = payload.get("action_requests")
        if isinstance(requests, list) and requests and isinstance(requests[0], dict):
            return str(requests[0].get("name") or "")
    return ""


def _approval_resume(
    decision: str,
    *,
    tool_name: str = "",
    edited_args: dict[str, Any] | None,
    message: str | None,
) -> dict[str, Any]:
    """把 API 决策转成 langchain HITL **batched** resume 形状 `{"decisions":[Decision]}`。

    实测（deepagents 0.7.23 / langchain 1.4.7）：
    - approve → `{"type":"approve"}`
    - edit    → `{"type":"edit","edited_action":{"name":..,"args":{..}}}`
                `name` 必须是 agent 已注册的工具名（`_apply_edit` 会校验，传错抛 ValueError）
    - reject  → `{"type":"reject","message":...}`
    - respond → `{"type":"respond","message":...}`
    """
    if decision == "approve":
        return {"decisions": [{"type": "approve"}]}
    if decision == "edit":
        return {
            "decisions": [
                {
                    "type": "edit",
                    "edited_action": {"name": tool_name, "args": edited_args or {}},
                }
            ]
        }
    if decision == "reject":
        item: dict[str, Any] = {"type": "reject"}
        if message:
            item["message"] = message
        return {"decisions": [item]}
    # respond
    return {"decisions": [{"type": "respond", "message": message or ""}]}


# ---------------------------------------------------------------------------
# 校验工具
# ---------------------------------------------------------------------------


async def _require_session(session: AsyncSession, session_id: str) -> AgentSession:
    row = await session.get(AgentSession, session_id)
    if row is None:
        raise NotFoundError(f"会话不存在：{session_id}")
    return row


async def require_session_of_project(
    session: AsyncSession, session_id: str, project_id: str
) -> AgentSession:
    """校验会话归属项目（防跨项目访问）。"""
    row = await _require_session(session, session_id)
    if row.project_id != project_id:
        from app.core.errors import ForbiddenError

        raise ForbiddenError(f"会话 {session_id} 不属于项目 {project_id}")
    return row
