# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.resolve
文件名: resolve.py
作者: xionggaoqing
时间: 2026-10-27
说明: 运行前的解析：主模型与降级链、上下文窗口、Langfuse 回调、前情提要 provider、
    会话级子代理/技能上下文（手册「二、(1)」「三、(3) 输入窗口」）。

    普通运行与门禁运行共用；从 runner 拆出以隔离「装配前解析」与「run 生命周期」两类职责。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.knowledge import build_pinned_chapters, build_recap
from app.agent.llm.factory import get_chat_model, get_default_chat_model, get_fallback_chain
from app.agent.tracing import build_callback_handler
from app.core.logging import get_logger
from app.db.models import AgentSession, SubagentConfig
from app.db.session import session_scope

logger = get_logger(__name__)


async def resolve_models(run_id: str, model_ref: str | None) -> tuple[Any, str | None, list[Any]]:
    """解析主模型 + 降级链。未配置模型时抛 LlmProviderError（由上层转 error 事件）。"""

    async with session_scope() as db:
        if model_ref:
            chat_model = await get_chat_model(db, model_ref)
            chain = await get_fallback_chain(db, model_ref)
        else:
            model_ref, chat_model = await get_default_chat_model(db)
            chain = await get_fallback_chain(db, model_ref)

        fallbacks: list[Any] = []
        for ref in chain[1:4]:  # 主模型之外最多 3 级降级
            try:
                fallbacks.append(await get_chat_model(db, ref))
            except Exception as exc:
                logger.warning("fallback_model_skip", model_ref=ref, error=str(exc))
        return chat_model, model_ref, fallbacks


def max_context_of(model: Any) -> int:
    for attr in ("max_context", "max_tokens", "context_window"):
        value = getattr(model, attr, None)
        if isinstance(value, int) and value > 0:
            return value
    return 32768


def attach_langfuse(config: dict[str, Any], **metadata: Any) -> None:
    """挂 langchain CallbackHandler（未配置 Key 时自动跳过）。"""

    handler = build_callback_handler(**metadata)
    if handler is not None:
        config["callbacks"] = [handler]


def make_recap_provider(project_id: str, session_id: str) -> Any:
    async def _provider(_context: Any) -> str:

        async with session_scope() as db:
            recap = await build_recap(db, project_id)
            # 会话级「已读章节」上下文 Chips（手册「三、(3) 输入窗口」）
            pinned = await _pinned_chapters_of(db, session_id, project_id)
            if pinned:
                recap = f"{recap}\n\n{pinned}" if recap else pinned
            return recap

    return _provider


async def _pinned_chapters_of(db: AsyncSession, session_id: str, project_id: str) -> str:

    sess = await db.get(AgentSession, session_id)
    if sess is None or not sess.context_chapter_ids_json:
        return ""
    try:
        ids = json.loads(sess.context_chapter_ids_json)
    except ValueError:
        return ""
    if not isinstance(ids, list) or not ids:
        return ""
    try:
        return await build_pinned_chapters(db, project_id, [str(x) for x in ids])
    except Exception as exc:  # 上下文注入失败不阻断运行
        logger.warning("pinned_chapters_failed", session_id=session_id, error=str(exc))
        return ""


async def session_context_of(db: AsyncSession, session_id: str) -> tuple[set[str], set[str]]:
    """读会话级子代理/技能选择，把 id 解析为 name（空集 = 用全局默认，不额外过滤）。

    - 子代理 id → `subagent_configs.name`；
    - 技能 id 可为 DB uuid（用户技能 → name）或 `builtin:{name}`（内置技能 → name）。
    """

    sess = await db.get(AgentSession, session_id)
    if sess is None:
        return set(), set()

    def _parse_ids(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            value = json.loads(raw)
        except ValueError:
            return []
        return [str(x) for x in value] if isinstance(value, list) else []

    sub_ids = _parse_ids(sess.subagent_ids_json)
    skill_ids = _parse_ids(sess.skill_ids_json)

    sub_names: set[str] = set()
    if sub_ids:
        rows = (
            await db.execute(select(SubagentConfig.name).where(SubagentConfig.id.in_(sub_ids)))
        ).scalars().all()
        sub_names = set(rows)

    skill_names: set[str] = set()
    for sid in skill_ids:
        if sid.startswith("builtin:"):
            skill_names.add(sid.split(":", 1)[1])
        else:
            from app.db.models import SkillConfig

            name = (
                await db.execute(select(SkillConfig.name).where(SkillConfig.id == sid))
            ).scalar_one_or_none()
            if name:
                skill_names.add(name)

    return sub_names, skill_names
