# -*- coding: utf-8 -*-
"""
模块: app.agent.tracing.messages.tree
文件名: tree.py
作者: xionggaoqing
时间: 2026-10-27
说明: 消息树与会话持久化（手册「二、(4) Message 模块」）。

    - 消息树：`messages.parent_id + branch_index`，会话保存 `active_leaf_id`；
    - 「重新生成」= 同父节点下新增兄弟分支；「编辑用户消息后重发」= 新建分支；
    - 构造 LLM 上下文时只取 `root → active_leaf` 的活跃路径；
    - 持久化双轨：业务库（渲染/审计/导出）+ LangGraph `AsyncSqliteSaver`（恢复/HITL 续跑）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models import AgentSession, Message

logger = get_logger(__name__)


def thread_id_of(project_id: str, session_id: str) -> str:
    """LangGraph `thread_id`（手册「二、(2)」格式）。"""
    return f"novel:{project_id}:session:{session_id}"


# ---------------------------------------------------------------------------
# 消息落库
# ---------------------------------------------------------------------------


async def add_message(
    session: AsyncSession,
    *,
    session_id: str,
    role: str,
    content: Any,
    parent_id: str | None = None,
    run_id: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    status: str = "complete",
    tokens_in: int = 0,
    tokens_out: int = 0,
    model: str | None = None,
) -> Message:
    """追加一条消息。`parent_id` 为空时自动接在当前 active_leaf 之后。"""
    row_session = await session.get(AgentSession, session_id)
    if row_session is None:
        raise ValueError(f"会话不存在：{session_id}")

    if parent_id is None:
        parent_id = row_session.active_leaf_id

    branch_index = 0
    if parent_id is not None:
        siblings = (
            await session.execute(
                select(Message.branch_index).where(
                    Message.session_id == session_id, Message.parent_id == parent_id
                )
            )
        ).scalars().all()
        branch_index = (max(siblings) + 1) if siblings else 0

    content_json = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)

    row = Message(
        id=new_id(),
        session_id=session_id,
        parent_id=parent_id,
        branch_index=branch_index,
        role=role,
        content_json=content_json,
        tool_calls_json=json.dumps(tool_calls, ensure_ascii=False, default=str) if tool_calls else None,
        status=status,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model=model,
        run_id=run_id,
        created_at=utcnow(),
    )
    session.add(row)
    await session.flush()

    row_session.active_leaf_id = row.id
    row_session.updated_at = utcnow()
    return row


async def set_active_leaf(session: AsyncSession, session_id: str, message_id: str) -> None:
    """切换分支（`POST /agent/sessions/{sid}/branch`）。"""
    row = await session.get(AgentSession, session_id)
    if row is None:
        raise ValueError(f"会话不存在：{session_id}")
    msg = await session.get(Message, message_id)
    if msg is None or msg.session_id != session_id:
        raise ValueError(f"消息不属于该会话：{message_id}")
    row.active_leaf_id = message_id
    row.updated_at = utcnow()


# ---------------------------------------------------------------------------
# 消息树读取
# ---------------------------------------------------------------------------


async def load_tree(session: AsyncSession, session_id: str) -> dict[str, Any]:
    """返回消息树 DTO：全部消息 + active 路径 + 兄弟分支信息。"""
    rows = (
        (
            await session.execute(
                select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    by_id = {r.id: r for r in rows}
    row_session = await session.get(AgentSession, session_id)
    active_leaf = row_session.active_leaf_id if row_session else None

    children: dict[str | None, list[Message]] = {}
    for r in rows:
        children.setdefault(r.parent_id, []).append(r)
    for group in children.values():
        group.sort(key=lambda m: (m.branch_index, m.created_at))

    active_path = _active_path(by_id, active_leaf)
    active_ids = {m.id for m in active_path}

    items = []
    for r in rows:
        siblings = children.get(r.parent_id, [])
        items.append(
            {
                "id": r.id,
                "parent_id": r.parent_id,
                "branch_index": r.branch_index,
                "branch_count": len(siblings),
                "role": r.role,
                "content": _content_of(r),
                "tool_calls": _json_of(r.tool_calls_json),
                "status": r.status,
                "tokens_in": r.tokens_in,
                "tokens_out": r.tokens_out,
                "model": r.model,
                "run_id": r.run_id,
                "created_at": r.created_at,
                "in_active_path": r.id in active_ids,
            }
        )

    return {
        "session_id": session_id,
        "active_leaf_id": active_leaf,
        "total": len(items),
        "items": items,
    }


def _content_of(row: Message) -> Any:
    raw = row.content_json
    if not raw:
        return ""
    if raw.startswith(("{", "[")):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _json_of(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _active_path(by_id: dict[str, Message], leaf_id: str | None) -> list[Message]:
    """root → active_leaf 路径（只取活跃分支）。"""
    path: list[Message] = []
    current = by_id.get(leaf_id) if leaf_id else None
    guard = 0
    while current is not None and guard < 10000:
        path.append(current)
        current = by_id.get(current.parent_id) if current.parent_id else None
        guard += 1
    path.reverse()
    return path


def active_path_to_langchain(by_id: dict[str, Message], leaf_id: str | None) -> list[BaseMessage]:
    """活跃路径 → LangChain 消息列表（用于重建上下文 / 断线恢复）。"""
    out: list[BaseMessage] = []
    for row in _active_path(by_id, leaf_id):
        content = _content_of(row)
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        if row.role == "user":
            out.append(HumanMessage(content=str(content)))
        elif row.role == "assistant":
            tool_calls = _json_of(row.tool_calls_json) or []
            out.append(
                AIMessage(
                    content=str(content or ""),
                    tool_calls=[tc for tc in tool_calls if isinstance(tc, dict)],
                )
            )
        elif row.role == "tool":
            out.append(ToolMessage(content=str(content or ""), tool_call_id=row.id))
        else:
            out.append(SystemMessage(content=str(content or "")))
    return out


async def history_messages_for_seed(
    session: AsyncSession, session_id: str, *, exclude_id: str | None = None
) -> list[BaseMessage]:
    """活跃路径 → LangChain 消息列表。

    与 `active_path_to_langchain` 的区别：tool 消息的 `tool_call_id` 与其父 assistant
    消息的 tool_calls **按序配对**（而非用消息 id），保证 LangGraph 校验通过；
    system 行（中间件注入残留）跳过。
    """
    rows = (
        (
            await session.execute(
                select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    by_id = {r.id: r for r in rows}
    row_session = await session.get(AgentSession, session_id)
    leaf = row_session.active_leaf_id if row_session else None

    children: dict[str | None, list[Message]] = {}
    for r in rows:
        children.setdefault(r.parent_id, []).append(r)
    for group in children.values():
        group.sort(key=lambda m: (m.branch_index, m.created_at))

    out: list[BaseMessage] = []
    for row in _active_path(by_id, leaf):
        if row.id == exclude_id or row.role == "system":
            continue
        content = _content_of(row)
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        if row.role == "user":
            out.append(HumanMessage(content=str(content)))
        elif row.role == "assistant":
            tcs = [tc for tc in (_json_of(row.tool_calls_json) or []) if isinstance(tc, dict)]
            out.append(AIMessage(content=str(content or ""), tool_calls=tcs))
            if tcs:
                tool_kids = [k for k in children.get(row.id, []) if k.role == "tool"]
                for tc, kid in zip(tcs, tool_kids, strict=False):
                    kc = _content_of(kid)
                    if isinstance(kc, list):
                        kc = json.dumps(kc, ensure_ascii=False)
                    out.append(ToolMessage(content=str(kc or ""), tool_call_id=str(tc.get("id") or kid.id)))
        # 游离的 tool 行（父 assistant 被跳过等）不入种子，避免孤儿 ToolMessage
    return out


async def sibling_branches(session: AsyncSession, session_id: str, message_id: str) -> list[dict[str, Any]]:
    """同父兄弟分支（前端「切换分支」用）。"""
    msg = await session.get(Message, message_id)
    if msg is None:
        return []
    rows = (
        (
            await session.execute(
                select(Message)
                .where(Message.session_id == session_id, Message.parent_id == msg.parent_id)
                .order_by(Message.branch_index.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "branch_index": r.branch_index,
            "role": r.role,
            "status": r.status,
            "created_at": r.created_at,
            "excerpt": str(_content_of(r))[:80],
        }
        for r in rows
    ]
