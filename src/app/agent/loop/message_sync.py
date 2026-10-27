# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.message_sync
文件名: message_sync.py
作者: xionggaoqing
时间: 2026-10-27
说明: 消息树增量同步：把 graph state 的 messages 落到业务库 `messages`（手册「二、(4)」）。

    普通运行与门禁 G2 共用；从 runner 拆出以隔离「消息树落库」与「run 生命周期」两类职责。
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.memory import add_message
from app.core.logging import get_logger
from app.db.models import AgentSession
from app.db.session import session_scope

logger = get_logger(__name__)


class MessageSyncer:
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
