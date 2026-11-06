# -*- coding: utf-8 -*-
"""
模块: app.agent.tracing.messages.message_sync
文件名: message_sync.py
作者: xionggaoqing
时间: 2026-10-27
说明: 消息树增量同步：把 graph state 的 messages 落到业务库 `messages`（手册「二、(4)」）。

    普通运行与流程 S2 共用；从 runner 拆出以隔离「消息树落库」与「run 生命周期」两类职责。
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.harness.llm.budget import cached_tokens_of
from app.agent.tracing.messages.tree import add_message
from app.core.logging import get_logger
from app.db.models import AgentSession
from app.db.session import session_scope

logger = get_logger(__name__)


class MessageSyncer:
    """把 graph state 的 messages 增量同步到业务库 `messages`（保持树结构）。

    按**索引基线**增量同步（不按消息 id 匹配）：graph state 里的消息 id 由 LangGraph 生成，
    与业务库 uuid 无对应关系；而 state 的 messages 是**追加式**的，故只需记录「已同步到的下标」，
    之后每次 sync 只处理新增部分。

    基线确定：
    - 新消息：基线 = state 长度；本轮用户消息已由 API 层落库，sync 时按正文跳过其副本一次；
    - 恢复：中断前的消息已全部落库 → 基线 = 当前 state 长度，parent = 会话 active_leaf_id。
    """

    def __init__(self, *, session_id: str) -> None:
        self.session_id = session_id
        self._index = 0
        self._last_leaf: str | None = None
        #: 本轮 API 已落库的用户消息正文（跳过它在 state 里的副本，仅一次）
        self._api_user_text: str | None = None
        #: 流程 S2：跳过下一条 human 消息（内部生成指令不入消息树）
        self.skip_human_once = False

    async def init_baseline(
        self,
        graph: Any,
        config: dict[str, Any],
        *,
        user_message_id: str | None,
        resumed: bool,
    ) -> None:
        """确定同步基线与父节点。

        基线 = **当前 state 长度**（checkpoint 跨轮保留历史）。
        旧实现假设线程全新（`index = 1`），导致同会话第二轮起把整段历史
        当作「新增」重复落库——前端看起来就是「一条消息回了两次」。
        本轮用户消息已由 API 层落库：记住正文，sync 时跳过它在 state 里的副本。
        """
        self._index = await self._state_len(graph, config)
        if resumed:
            self._last_leaf = await self._active_leaf()
        else:
            self._last_leaf = user_message_id
            if user_message_id:
                self._api_user_text = await self._user_text_of(user_message_id)

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

        # 流程内部指令消息不入树（只推进指针）
        if self.skip_human_once and _role_of(new_messages[0]) == "user":
            new_messages = new_messages[1:]
            self._index += 1
            self.skip_human_once = False
            if not new_messages:
                return

        # 中间件注入的 system（前情提要等）、**内部注入的 human**（steer 引导 /
        # 系统守护纠正 / 收尾 nudge）与**本轮 API 已落库的用户消息副本**
        # 不入消息树——只推进指针。否则前端会把前情提要/守护提醒渲染成气泡，
        # 重复 user 还会把一轮问答拆成两轮（中间思考文本被当成「最终回复」展示）。
        filtered: list[Any] = []
        for msg in new_messages:
            role = _role_of(msg)
            if role == "system":
                self._index += 1
                continue
            if role == "user" and _is_internal_human(msg):
                self._index += 1
                continue
            if role == "user" and self._api_user_text is not None and _text_of(msg) == self._api_user_text:
                self._api_user_text = None  # 只跳过一次
                self._index += 1
                continue
            filtered.append(msg)
        new_messages = filtered
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
                        cached_in=cached_tokens_of(usage),
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

    @staticmethod
    async def _user_text_of(message_id: str) -> str | None:
        """取业务库中某条消息的正文（首轮基线去重用）。"""
        from app.db.models import Message

        async with session_scope() as db:
            row = await db.get(Message, message_id)
            return _text_of(row.content_json) if row is not None else None

    async def _active_leaf(self) -> str | None:
        async with session_scope() as db:
            sess = await db.get(AgentSession, self.session_id)
            return sess.active_leaf_id if sess is not None else None


def _is_incomplete(msg: Any) -> bool:
    """流式中途的消息（无内容且无 tool_calls）视为未完成。"""
    if getattr(msg, "status", None) == "streaming":
        return True
    return not getattr(msg, "content", "") and not getattr(msg, "tool_calls", None)


#: 内部注入的 human 消息前缀（系统守护纠正 / 预算补充指示 / 收尾 nudge）
#: 用开括号前缀匹配——覆盖「（系统守护）」与「（系统守护·熔断）」两种文案
_INTERNAL_HUMAN_PREFIXES = ("（系统守护", "（系统修复", "（用户补充指示")


def _is_internal_human(msg: Any) -> bool:
    """内部注入的 human（steer 引导 / 守护纠正 / nudge）不入消息树、不展示。"""
    if getattr(msg, "name", None) == "steer":
        return True
    text = _text_of(msg)
    return any(text.startswith(p) for p in _INTERNAL_HUMAN_PREFIXES)


def _text_of(msg_or_raw: Any) -> str:
    """消息对象或落库的 content_json 字符串 → 纯文本（去重比较用）。"""
    content = msg_or_raw
    if not isinstance(content, str):
        content = getattr(msg_or_raw, "content", "")
    raw = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    # 落库格式可能是 JSON 字符串化的 list/dict，这里只做轻量解析
    if isinstance(raw, str) and raw[:1] in ("[", "{"):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return raw
        if isinstance(parsed, list):
            parts = []
            for block in parsed:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    parts.append(str(block.get("text") or block.get("content") or ""))
            return "".join(parts)
        if isinstance(parsed, dict):
            return str(parsed.get("text") or parsed.get("content") or "")
    return raw if isinstance(raw, str) else str(raw)


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
