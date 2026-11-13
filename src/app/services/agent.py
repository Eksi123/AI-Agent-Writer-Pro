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
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tracing.messages import load_tree, set_active_leaf
from app.core.errors import ApiError, ErrorCode, NotFoundError
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models import AgentInterrupt, AgentRun, AgentSession, Message, Novel, ProjectIndex, ToolCallLog
from app.db.session import session_scope
from app.services import audit

logger = get_logger(__name__)

#: services → agent 的**能力型依赖入口**（消息树读写）。
#: 消息树是 Agent 的记忆载体，读写实现留在 agent/memory；agent/memory 不 import services，
#: 故此顶层导入无环。这是刻意保留的少数反向依赖之一（详见 Writer-Develop.md「2.3 依赖方向与边界」）。

#: run 终态（不可再 resolve / cancel）
_TERMINAL_RUN_STATUS = ("completed", "failed", "cancelled")

#: approval 决策 → langchain HITL batched resume 形状
#: `approve_session` = 读取授权守卫专用（本次会话内放行同类读取，见 read_scope）
_APPROVAL_DECISIONS = ("approve", "approve_session", "edit", "reject", "respond")
#: consult 决策
_CONSULT_DECISIONS = ("option", "text")


# ---------------------------------------------------------------------------
# 会话 CRUD
# ---------------------------------------------------------------------------


def summarize_session_title(content: str, *, max_len: int = 30) -> str:
    """首条用户消息 → 会话标题。

    本地规则总结（不额外调 LLM）：取首个问句/首行，压缩空白，截断加省略号。
    """
    text = (content or "").strip()
    if not text:
        return "新会话"
    # 优先取第一个问句（以 ？/? 结尾），其次取首行
    first = ""
    for seg in re.split(r"[。！？!?\n]", text):
        seg = seg.strip()
        if seg:
            first = seg
            break
    first = re.sub(r"\s+", " ", first)
    if len(first) > max_len:
        first = first[: max_len - 1] + "…"
    return first or "新会话"


async def project_usage(db: AsyncSession, project_id: str) -> dict[str, Any]:
    """项目级用量（会话数量 + 该项目 token）。

    token 以 `agent_runs` 为准而非 `messages`——子代理默认走**独立上下文**，
    其消息不进主消息树，但运行级 `tokens_in/out` 已把子代理用量累计进去
    （见 `agent/loop/streaming.py` 对带 namespace 的 chunk 同样累加 usage），
    因此按 run 汇总才满足「和主 Agent 一起算 Token 消耗」。
    `run.tokens_in` 是收尾时整体赋值（非累加），逐 run 求和不会重复计数。

    ★ 删除会话时 run 记录保留（session_id 清空），仍计入项目级 token 统计。
    ★ session_count 与 list_sessions 返回的条数一致（同查询条件），避免前端
      「N 个会话」标题与实际展示的会话列表条数不一致。
    """
    # 用与 list_sessions 相同的查询条件统计会话数
    stmt = select(func.count(AgentSession.id)).where(AgentSession.project_id == project_id)
    session_count = (await db.execute(stmt)).scalar() or 0

    # 获取所有会话 ID 用于查询 run
    sessions = (
        (await db.execute(select(AgentSession).where(AgentSession.project_id == project_id))).scalars().all()
    )
    session_ids = [s.id for s in sessions]

    # 查询所有属于该项目的 run（包括孤儿 run，即 session_id 为空的）
    tokens_in = 0
    tokens_out = 0
    cached_in = 0

    # 先查有 session 的 run
    if session_ids:
        rows = (
            await db.execute(
                select(
                    func.sum(AgentRun.tokens_in),
                    func.sum(AgentRun.tokens_out),
                    func.sum(AgentRun.cached_in),
                ).where(AgentRun.session_id.in_(session_ids))
            )
        ).first()
        tokens_in = int(rows[0] or 0)
        tokens_out = int(rows[1] or 0)
        cached_in = int(rows[2] or 0)

    # 再查孤儿 run（session_id 为空但 project_id 匹配的）
    orphan_rows = (
        await db.execute(
            select(
                func.sum(AgentRun.tokens_in),
                func.sum(AgentRun.tokens_out),
                func.sum(AgentRun.cached_in),
            ).where(
                AgentRun.project_id == project_id,
                (AgentRun.session_id == "") | (AgentRun.session_id.is_(None))
            )
        )
    ).first()
    tokens_in += int(orphan_rows[0] or 0)
    tokens_out += int(orphan_rows[1] or 0)
    cached_in += int(orphan_rows[2] or 0)

    return {
        "project_id": project_id,
        "session_count": session_count,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "cached_in": cached_in,
    }


async def app_usage(db: AsyncSession) -> dict[str, Any]:
    """应用级 Token 用量（跨所有项目累计）。

    会话数量仍是项目级（前端传入 project_id 查询），但 Token 消耗是全局的。
    """
    rows = (
        await db.execute(
            select(
                func.sum(AgentRun.tokens_in),
                func.sum(AgentRun.tokens_out),
                func.sum(AgentRun.cached_in),
            )
        )
    ).first()
    tokens_in = int(rows[0] or 0)
    tokens_out = int(rows[1] or 0)
    cached_in = int(rows[2] or 0)
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "cached_in": cached_in,
    }


async def session_usage(db: AsyncSession, session_id: str) -> dict[str, Any]:
    """会话级上下文占用。

    取**最近一次模型调用**的输入 token（消息树里最后一条带 usage 的 assistant
    消息）作为当前上下文占用——它正是该次调用的真实输入（system prompt + 历史 +
    工具结果），最贴近「窗口已用多少」。

    注意**不能**用 `agent_runs.tokens_in`：那是整轮 run 所有模型调用输入 token 的
    **累加**（每次调用都重发全量历史），一次长 run 可达数十万，远超窗口——拿它当
    占用会让前端「上下文使用情况」虚高甚至超过窗口（剩余被夹到 0%）。

    没有任何带 usage 的消息时返回 0（前端显示 100% 剩余）。
    """
    row = (
        await db.execute(
            select(Message.tokens_in, Message.tokens_out, Message.cached_in)
            .where(
                Message.session_id == session_id,
                Message.role == "assistant",
                Message.tokens_in > 0,
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
    ).first()
    tokens_in = int(row[0] or 0) if row else 0
    tokens_out = int(row[1] or 0) if row else 0
    cached_in = int(row[2] or 0) if row else 0
    return {
        "session_id": session_id,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cached_in": cached_in,
    }


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
    row = await require_session(session, session_id)
    new_title = (title or "").strip()
    if not new_title:
        from app.core.errors import BadRequestError

        raise BadRequestError("会话标题不能为空")
    row.title = new_title
    row.updated_at = utcnow()
    await session.flush()
    return _session_dto(row)


async def delete_session(session: AsyncSession, session_id: str) -> dict[str, Any]:
    row = await require_session(session, session_id)
    if row.status == "running":
        raise ApiError("会话正在运行中，请先取消运行再删除", code=ErrorCode.RUN_STATE_CONFLICT)
    project_id = row.project_id
    # 删除会话时保留其关联的 run 记录（token 消耗数据不随会话删除而丢失）
    # 将 run 的 session_id 清空，标记为孤儿记录，仍计入项目级 token 统计
    from app.db.models.agent import AgentRun
    await session.execute(
        AgentRun.__table__.update()
        .where(AgentRun.session_id == session_id)
        .values(session_id="")
    )
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
    row = await require_session(session, session_id)
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


async def load_message_tree(session: AsyncSession, session_id: str) -> dict[str, Any]:
    """消息树 DTO + 会话头信息（`GET /agent/sessions/{sid}/messages`）。

    树结构由 `agent/memory` 读写（消息树是 Agent 的记忆载体），本函数只负责
    把会话标题/状态/项目一并拼进响应，使 API 层不必直连 agent 内部。

    附带 `latest_run`（最近一次 run 的 id/status 与**未决中断**）——
    审批/咨询卡片原先只靠 SSE 事件推送，页面刷新/重进会话即丢失，run 永久挂起；
    前端进入会话时据此恢复卡片并重连事件流。
    """
    row = await require_session(session, session_id)
    tree = await load_tree(session, session_id)
    tree["title"] = row.title
    tree["status"] = row.status
    tree["project_id"] = row.project_id

    run_row = (
        (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.session_id == session_id)
                .order_by(AgentRun.started_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    tree["latest_run"] = None
    if run_row is not None:
        # run 已结束（completed/failed/cancelled）时，未决中断是"孤儿"——
        # 无法再 resume，前端展示只会让用户反复点批准然后收到"运行已结束"错误。
        # 故终端态 run 不返回 pending_interrupts，直接标记 latest_run 为 None。
        if run_row.status in _TERMINAL_RUN_STATUS:
            tree["latest_run"] = {
                "run_id": run_row.id,
                "status": run_row.status,
                "pending_interrupts": [],
            }
        else:
            pending = (
                (
                    await session.execute(
                        select(AgentInterrupt)
                        .where(AgentInterrupt.run_id == run_row.id, AgentInterrupt.decision.is_(None))
                        .order_by(AgentInterrupt.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )
            # 如果 run 状态是 awaiting_consult/awaiting_approval，但没有未决策的中断，
            # 说明所有中断都已决策，run 应该继续执行。此时把状态改为 running，
            # 避免前端误以为还有未决策的中断，从而错误地恢复卡片。
            status = run_row.status
            if status in ("awaiting_consult", "awaiting_approval") and not pending:
                status = "running"
            # 获取当前 SSE 缓冲区的最新 seq，供前端重连时作为 last_event_id，
            # 避免补发旧事件导致已决策的卡片重新弹出。
            current_seq = 0
            if status not in _TERMINAL_RUN_STATUS:
                from app.agent.tracing.sse.events import sse_bus
                current_seq = sse_bus.get_seq(run_row.id)
            tree["latest_run"] = {
                "run_id": run_row.id,
                "status": status,
                "pending_interrupts": [_interrupt_dto(i) for i in pending],
                "current_seq": current_seq,
            }
    return tree


async def switch_branch(session: AsyncSession, session_id: str, message_id: str) -> dict[str, Any]:
    """切换 active_leaf_id（分支查看 / 重新生成前的回退）。"""
    await require_session(session, session_id)
    try:
        await set_active_leaf(session, session_id, message_id)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    await session.flush()

    row = await session.get(AgentSession, session_id)
    return {"session_id": session_id, "active_leaf_id": row.active_leaf_id if row else None}


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

    workflow_result: Any = None
    if run.workflow_result_json:
        try:
            workflow_result = json.loads(run.workflow_result_json)
        except ValueError:
            workflow_result = run.workflow_result_json

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
        "workflow_stage": run.workflow_stage,
        "workflow_retry_count": run.workflow_retry_count,
        "workflow_result": workflow_result,
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
        # 幂等处理：如果决策相同，视为重复提交，返回成功而不是抛错
        if row.decision == decision:
            run = await session.get(AgentRun, row.run_id)
            if run is None:
                raise NotFoundError(f"run 不存在：{row.run_id}")
            if run.status in _TERMINAL_RUN_STATUS:
                return {
                    "run_id": run.id,
                    "resume_value": None,
                    "run_ended": True,
                    "run_status": run.status,
                }
            # run 仍在运行：重复提交同一决策——首次 resolve 已驱动续跑，
            # 此处只确认受理，不再二次 resume（否则同一 run 被并发续跑两次）。
            return {
                "run_id": run.id,
                "resume_value": None,
                "run_ended": False,
                "duplicate": True,
            }
        raise ApiError(
            f"该中断已处理（{row.decision}）",
            code=ErrorCode.RUN_STATE_CONFLICT,
            data={"interrupt_id": interrupt_id, "decision": row.decision},
        )

    run = await session.get(AgentRun, row.run_id)
    if run is None:
        raise NotFoundError(f"run 不存在：{row.run_id}")
    if run.status in _TERMINAL_RUN_STATUS:
        # run 已结束，记录中断决定但不抛错，让用户重新发送消息
        row.decision = decision
        row.response_json = json.dumps(
            {"decision": decision, "message": "run 已结束，请重新发送消息"},
            ensure_ascii=False, default=str
        )
        row.decided_by = "user"
        row.decided_at = utcnow()
        await session.flush()
        # 返回一个特殊的 resume_value 告诉前端 run 已结束
        return {
            "run_id": run.id,
            "resume_value": None,
            "run_ended": True,
            "run_status": run.status,
        }

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
    - approve_session → `{"type":"approve_session"}`（读取守卫：本次会话内放行同类读取）
    - edit    → `{"type":"edit","edited_action":{"name":..,"args":{..}}}`
                `name` 必须是 agent 已注册的工具名（`_apply_edit` 会校验，传错抛 ValueError）
    - reject  → `{"type":"reject","message":...}`
    - respond → `{"type":"respond","message":...}`
    """
    if decision in ("approve", "approve_session"):
        return {"decisions": [{"type": decision}]}
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


async def require_session(session: AsyncSession, session_id: str) -> AgentSession:
    """取会话行，不存在则 404。

    会话取数的唯一入口：API 层、本模块其余函数与 agent 运行层（loop/runner）共用。
    """
    row = await session.get(AgentSession, session_id)
    if row is None:
        raise NotFoundError(f"会话不存在：{session_id}")
    return row


async def require_session_of_project(
    session: AsyncSession, session_id: str, project_id: str
) -> AgentSession:
    """校验会话归属项目（防跨项目访问）。"""
    row = await require_session(session, session_id)
    if row.project_id != project_id:
        from app.core.errors import ForbiddenError

        raise ForbiddenError(f"会话 {session_id} 不属于项目 {project_id}")
    return row


# ---------------------------------------------------------------------------
# 数据访问（会话列表 / 工具调用日志 / 项目字数）
# ---------------------------------------------------------------------------
#
# 这一组是纯取数逻辑，住在服务层，使 14 个 API 模块取数路径一致（都经 services）；
# agent 运行层（loop/runner、chapter_workflow/runner）也复用这里的 `project_word_count`。


async def list_sessions(db: AsyncSession, project_id: str | None = None) -> list[dict[str, Any]]:
    """会话列表（按项目隔离）。

    DTO 复用 `_session_dto`，确保与单会话读写返回同构
    （含 subagent_ids / skill_ids / context_chapter_ids 上下文 Chips）。
    """
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
    """工具调用日志（运行日志面板）。"""
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


async def project_word_count(project_id: str) -> int:
    """项目派生索引里的总字数（Langfuse score 用）。"""
    async with session_scope() as db:
        row = await db.get(ProjectIndex, project_id)
        return int(row.total_words) if row is not None else 0
