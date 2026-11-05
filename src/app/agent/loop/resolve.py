# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.resolve
文件名: resolve.py
作者: xionggaoqing
时间: 2026-10-27
说明: 运行前的解析：主模型与降级链、上下文窗口、Langfuse 回调、前情提要 provider、
    会话级子代理/技能上下文（手册「二、(1)」「三、(3) 输入窗口」）。

    普通运行与固定流程运行共用；从 runner 拆出以隔离「装配前解析」与「run 生命周期」两类职责。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop.knowledge import build_pinned_chapters, build_recap
from app.agent.loop.llm.factory import get_chat_model, get_default_chat_model
from app.agent.more.langfuse import build_callback_handler
from app.core.logging import get_logger
from app.db.models import AgentSession, SubagentConfig
from app.db.session import session_scope
from app.services.skills import WEB_SEARCH_SKILL_NAME

logger = get_logger(__name__)


async def resolve_models(run_id: str, model_ref: str | None) -> tuple[Any, str | None, list[Any]]:
    """解析主模型 + 备用模型列表。

    备用模型 = 除主模型外**全部已启用模型**（按创建顺序），供
    `ModelSwitchOnFailureMiddleware` 在主模型连续失败达阈值时循环切换。
    未配置模型时抛 LlmProviderError（由上层转 error 事件）。
    """

    async with session_scope() as db:
        if model_ref:
            chat_model = await get_chat_model(db, model_ref)
        else:
            model_ref, chat_model = await get_default_chat_model(db)
        alternates = await get_alternate_models(db, model_ref)
        return chat_model, model_ref, alternates


async def get_alternate_models(session: AsyncSession, model_ref: str) -> list[Any]:
    """除主模型外的已启用模型实例（连续失败切换的候选）。"""
    from app.agent.loop.llm.factory import build_chat_model, resolve_model_ref
    from app.db.models import LlmModel

    try:
        provider, model = await resolve_model_ref(session, model_ref)
    except Exception:  # pragma: no cover - 主模型解析失败由上层处理
        return []
    rows = (
        (
            await session.execute(
                select(LlmModel)
                .where(LlmModel.enabled.is_(True), LlmModel.id != model.id)
                .order_by(LlmModel.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    alternates: list[Any] = []
    for row in rows:
        try:
            p = await session.get(type(provider), row.provider_id)
            if p is None or not getattr(p, "enabled", True):
                continue
            alternates.append(build_chat_model(p, row))
        except Exception as exc:
            logger.warning("alternate_model_skip", model_id=row.model_id, error=str(exc))
    return alternates


def max_context_of(model: Any) -> int:
    """上下文窗口（token）。优先读工厂写入的 `profile.max_input_tokens`
    （即模型行 `llm_models.max_context`，UI「Context Length」），再回落旧属性。"""
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        value = profile.get("max_input_tokens")
        if isinstance(value, int) and value > 0:
            return value
    for attr in ("max_context", "context_window"):
        value = getattr(model, attr, None)
        if isinstance(value, int) and value > 0:
            return value
    return 262_144


def attach_langfuse(config: dict[str, Any], **metadata: Any) -> None:
    """挂 langchain CallbackHandler（未配置 Key 时自动跳过）。"""

    handler = build_callback_handler(**metadata)
    if handler is not None:
        config["callbacks"] = [handler]


def make_recap_provider(project_id: str, session_id: str) -> Any:
    """前情提要注入 provider（**按需**注入，不常驻上下文）。

    默认不注入 recap（整本书摘要动辄数千 token，且每轮模型调用都重发）：
    仅当用户消息显式要求「读取/回顾章节内容」时才构建；否则返回空串。
    需要回顾已写内容时，主 Agent 可派发 `recaller` 子代理在隔离上下文里提取。

    会话级「已读章节」Chips（用户手动勾选）始终注入——那是用户的显式选择。
    """

    async def _provider(context: Any) -> str:
        from app.agent.loop.runner import run_manager
        from app.services.read_grant import wants_recap

        run_id = str(getattr(context, "run_id", "") or "")
        user_text = run_manager.user_text_of(run_id)

        async with session_scope() as db:
            recap = ""
            if wants_recap(user_text):
                recap = await build_recap(db, project_id)
            # 会话级「已读章节」上下文 Chips（手册「三、(3) 输入窗口」）
            pinned = await _pinned_chapters_of(db, session_id, project_id)
            if pinned:
                recap = f"{recap}\n\n{pinned}" if recap else pinned
            return recap

    return _provider


def make_outline_ctx_provider(project_id: str) -> Any:
    """目标章大纲摘录 provider（按需注入，代替整篇读大纲）。

    定位链：写作意图 / 显式提及章名 → `outline_recall.chapter_brief` 精确摘录
    「简介 + 衔接要点」（≤1200 字）。任一环缺失则返回空串（不注入），
    由主 Agent 自行按需调用 `outline_recall` 工具查询。
    固定流程运行（S0–S3）的生成提示词已带章简介，故由调用方决定是否装配本 provider。
    """

    async def _provider(context: Any) -> str:
        from app.agent.loop.knowledge.chapters import list_chapters_ordered
        from app.agent.loop.runner import run_manager
        from app.agent.workflows.writing_steps.intent import (
            resolve_mentioned_chapter,
            resolve_workflow_chapter,
        )
        from app.services.outline_recall import chapter_brief

        run_id = str(getattr(context, "run_id", "") or "")
        user_text = run_manager.user_text_of(run_id)
        if not (user_text or "").strip():
            return ""
        try:
            chapters = await list_chapters_ordered(project_id)
        except Exception as exc:  # 章节清单读取失败不阻断生成
            logger.warning("outline_ctx_chapters_failed", project_id=project_id, error=str(exc))
            return ""
        chapter_id = (
            resolve_workflow_chapter(user_text, chapters)
            if run_manager.read_intent_of(run_id)
            else None
        ) or resolve_mentioned_chapter(user_text, chapters)
        if not chapter_id:
            return ""
        meta = next((c for c in chapters if str(c.get("id")) == chapter_id), None)
        if meta is None:
            return ""

        async with session_scope() as db:
            brief = await chapter_brief(
                db,
                project_id,
                volume_title=str(meta.get("volume_title") or ""),
                chapter_title=str(meta.get("title") or ""),
            )
        if not brief:
            return ""
        return (
            "## 目标章大纲摘录\n"
            f"{brief}\n\n"
            "（需要其它卷章的简介/设定，调用 `outline_recall` 工具按需查询，"
            "不要 read_file 整篇大纲。）"
        )

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
    - 技能 id → `skill_configs.name`（技能 = 用户自建）；
    - `builtin:web_search`（会话级内置写作技能）→ 解析为 `web_search`，
      供消息前缀/@标记使用；主代理 `web_search` 工具恒可用，无需物化或额外装配；
    - 其它历史 `builtin:{name}` 前缀 id 已随包内技能目录移除而失效，静默跳过。
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
    real_ids = [sid for sid in skill_ids if not sid.startswith("builtin:")]
    if f"builtin:{WEB_SEARCH_SKILL_NAME}" in skill_ids:
        skill_names.add(WEB_SEARCH_SKILL_NAME)
    if real_ids:
        from app.db.models import SkillConfig

        rows = (
            await db.execute(select(SkillConfig.name).where(SkillConfig.id.in_(real_ids)))
        ).scalars().all()
        skill_names.update(rows)

    return sub_names, skill_names
