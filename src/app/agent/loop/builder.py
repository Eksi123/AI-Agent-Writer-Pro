#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.builder
文件名: builder.py
作者: xionggaoqing
时间: 2026-10-27
说明: `create_deep_agent` 装配（手册「二、(2) Agent-Loop」+「二、(5)」）。

    装配要点：
    - `backend`：CompositeBackend 三路由（`/projects/`、`/skills/builtin/`、`/skills/user/`）
    - `permissions`：8 条有序规则（`{pid}` 替换为当前项目），兜底 deny 最后
    - `checkpointer`：AsyncSqliteSaver（`data/agent_state.db`），按 thread_id 持久化
    - `context_schema`：AgentContext（project_id/session_id/run_id/... 经 Runtime.context 注入）
    - `middleware` 顺序（手册「二、(2)」）：
      ModelRetry → ModelFallback → ModelCallLimit → TodoList
      → RecapInjection → WorkspaceGuard → FileChangeNotify → WriteApproval
    - `skills`：`["/skills/builtin/", "/skills/user/"]`（虚拟路径，后者同名覆盖）
    - `subagents`：5 个内置写作子代理（general-purpose 由 deepagents 自动追加）
    - `interrupt_on`：delete 工具无条件审批（参数级高危写入由 WriteApprovalMiddleware 处理）

    **不引入** LocalShellBackend / LangSmithSandbox（会激活 execute 且与 permissions 冲突）。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    TodoListMiddleware,
)
from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop.context import AgentContext
from app.agent.loop.middleware import (
    FileChangeNotifyMiddleware,
    RecapInjectionMiddleware,
    WorkspaceGuardMiddleware,
    WriteApprovalMiddleware,
)
from app.agent.sandbox import build_backend, build_permissions
from app.core.config import current_config_version, get_settings
from app.core.logging import get_logger
from app.db.models import SkillConfig, SubagentConfig

logger = get_logger(__name__)

#: skills 虚拟路径（内置 + 用户自建，后者同名覆盖前者）
SKILL_PATHS = ["/skills/builtin/", "/skills/user/"]

#: delete 工具无条件审批（`interrupt_on` 只能做工具级开关，参数级见 WriteApprovalMiddleware）
INTERRUPT_ON: dict[str, Any] = {"delete": True}

#: 单次 run 的 LLM 调用软上限（硬上限另经 config recursion_limit）
DEFAULT_MODEL_CALL_LIMIT = 40


async def load_subagent_specs(session: AsyncSession) -> list[dict[str, Any]]:
    """从 DB 读取启用的子代理配置（内置已播种 + 用户自建）。

    DB 为空（如未播种的测试环境）时回落内置定义，保证 agent 始终有写作子代理可用。
    """
    from app.agent.subagents import BUILTIN_SUBAGENTS

    rows = (
        (
            await session.execute(
                select(SubagentConfig).where(SubagentConfig.enabled.is_(True)).order_by(SubagentConfig.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return [
            {
                "name": name,
                "description": spec["description"],
                "system_prompt": spec["system_prompt"],
                "mode": spec.get("mode", "isolated"),
                "skills": list(spec.get("skills") or []),
                "model_ref": None,
            }
            for name, spec in BUILTIN_SUBAGENTS.items()
        ]

    specs: list[dict[str, Any]] = []
    for row in rows:
        mode = row.mode or "isolated"
        skills = _loads_list(row.skills_json)
        if mode == "fork" and skills:
            logger.warning("subagent_fork_skills_cleared", name=row.name)
            skills = []
        specs.append(
            {
                "name": row.name,
                "description": row.description,
                "system_prompt": row.system_prompt or BUILTIN_SUBAGENTS.get(row.name, {}).get("system_prompt", ""),
                "mode": mode,
                "skills": skills,
                "model_ref": row.model_ref,
            }
        )
    return specs


def _loads_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def build_subagents_from_specs(
    specs: list[dict[str, Any]], resolved_models: dict[str, BaseChatModel]
) -> list[Any]:
    """把子代理 spec 列表装配为 deepagents `SubAgent` 列表（model_ref 预解析为 ChatModel 实例）。"""
    result: list[Any] = []
    for spec in specs:
        agent: dict[str, Any] = {
            "name": spec["name"],
            "description": spec["description"],
            "system_prompt": spec["system_prompt"],
            "mode": spec["mode"],
        }
        if spec["skills"]:
            agent["skills"] = spec["skills"]
        ref = spec.get("model_ref")
        if ref:
            chat_model = resolved_models.get(ref)
            if chat_model is not None:
                agent["model"] = chat_model
            else:
                logger.warning("subagent_model_unresolved", name=spec["name"], model_ref=ref)
        result.append(agent)
    return result


async def resolve_subagent_models(
    session: AsyncSession, model_refs: set[str]
) -> dict[str, BaseChatModel]:
    """把子代理需要的 model_ref 预解析为 ChatModel 实例（`SubAgent.model` 只接受实例）。"""
    from app.agent.llm.factory import get_chat_model

    resolved: dict[str, BaseChatModel] = {}
    for ref in model_refs:
        if not ref or ref in resolved:
            continue
        try:
            resolved[ref] = await get_chat_model(session, ref)
        except Exception as exc:
            logger.warning("subagent_model_skip", model_ref=ref, error=str(exc))
    return resolved


async def enabled_user_skills(session: AsyncSession) -> list[str]:
    """用户自建技能已物化到 `data/skills/`，经 `/skills/user/` 路由加载。

    这里返回 DB 中 enabled 的技能名（供日志与校验）；实际加载由 skills 路径完成。
    """
    rows = (
        await session.execute(select(SkillConfig.name).where(SkillConfig.enabled.is_(True)))
    ).scalars().all()
    return list(rows)


def build_middleware_stack(
    *,
    primary_model: BaseChatModel,
    fallback_models: list[BaseChatModel],
    recap_provider: Any = None,
    model_call_limit: int = DEFAULT_MODEL_CALL_LIMIT,
    write_approval_enabled: bool = True,
    max_context: int | None = None,
) -> list[Any]:
    """按手册顺序装配中间件栈。

    顺序（不可随意调换）：
    ModelRetry → ModelFallback → ModelCallLimit → TodoList
    → RecapInjection → WorkspaceGuard → FileChangeNotify → WriteApproval
    """
    stack: list[Any] = []

    # ① 重试：仅对超时 / 429 / 5xx / 连接错误重试（默认 retry_on 即覆盖这些）
    try:
        stack.append(ModelRetryMiddleware(max_retries=2, backoff_factor=2.0, jitter=True))
    except Exception as exc:  # pragma: no cover - 参数兼容性兜底
        logger.warning("model_retry_mw_fallback", error=str(exc))
        stack.append(ModelRetryMiddleware())

    # ② 降级链：主模型失败依次切换
    if fallback_models:
        try:
            from langchain.agents.middleware import ModelFallbackMiddleware

            stack.append(ModelFallbackMiddleware(primary_model, *fallback_models))
        except Exception as exc:  # pragma: no cover
            logger.warning("model_fallback_mw_unavailable", error=str(exc))

    # ③ 调用次数上限：优雅收尾
    try:
        stack.append(ModelCallLimitMiddleware(run_limit=model_call_limit, exit_behavior="end"))
    except Exception as exc:  # pragma: no cover
        logger.warning("model_call_limit_mw_unavailable", error=str(exc))

    # ④ 待办：提供 write_todos 工具 → plan.update 事件（deepagents 默认不带，显式加入）
    try:
        stack.append(TodoListMiddleware())
    except Exception as exc:  # pragma: no cover
        logger.warning("todo_list_mw_unavailable", error=str(exc))

    # ⑤ 上下文压缩：超阈值摘要（deepagents 版在 deepagents.middleware.summarization）
    summarization_mw = _build_summarization(primary_model, max_context=max_context)
    if summarization_mw is not None:
        stack.append(summarization_mw)

    # ⑥~⑨ 自定义中间件
    stack.append(RecapInjectionMiddleware(recap_provider=recap_provider))
    stack.append(WorkspaceGuardMiddleware())
    stack.append(FileChangeNotifyMiddleware())
    stack.append(WriteApprovalMiddleware(enabled=write_approval_enabled))

    return stack


def _build_summarization(model: BaseChatModel, *, max_context: int | None) -> Any | None:
    """上下文压缩中间件（deepagents 版：卸载驱逐消息 + 预截断大参数 + 溢出重试）。"""
    try:
        from deepagents.middleware.summarization import create_summarization_middleware

        return create_summarization_middleware(model, build_backend())
    except Exception as exc:
        logger.info("summarization_mw_unavailable", error=str(exc))
    # 回落 langchain 版
    try:
        from langchain.agents.middleware import SummarizationMiddleware

        tokens = int((max_context or 32768) * 0.8)
        return SummarizationMiddleware(model=model, trigger=("tokens", max(2000, tokens)))
    except Exception as exc:  # pragma: no cover
        logger.warning("summarization_fallback_unavailable", error=str(exc))
        return None


async def build_agent(
    session: AsyncSession,
    *,
    project_id: str,
    model: BaseChatModel,
    fallback_models: list[BaseChatModel] | None = None,
    recap_provider: Any = None,
    subagents_enabled: bool = True,
    tools_enabled: bool = True,
    write_approval_enabled: bool = True,
    recursion_limit: int = 60,
    session_id: str = "",
    subagent_names: set[str] | None = None,
    skill_names: set[str] | None = None,
) -> dict[str, Any]:
    """构建 deep agent。返回装配结果 dict（含 graph 与元信息，供 runner 使用）。

    调用方传入已解析的 `model`（主模型）与可选 `fallback_models`（降级链）。
    `subagent_names` / `skill_names`：会话级选择（手册「三、(3) 输入窗口」上下文 Chips），
    空/None = 用全局默认（全部启用子代理 + 全部技能目录）。`session_id` 用于会话技能精选目录挂载。
    """
    from deepagents import create_deep_agent

    from app.agent.prompts import render

    # 会话级技能精选：先物化，再挂进 backend 路由（手册「三、(3) 输入窗口」）
    session_skill_count = 0
    if session_id and skill_names:
        from app.services.skills import materialize_session_skills

        session_skill_count = await materialize_session_skills(session, session_id, skill_names)

    backend = build_backend(session_id if session_skill_count else "")
    permissions = build_permissions(project_id)

    # 工具：web_search（自研）+ 内置天气 MCP + 已启用的第三方 MCP（单个失败跳过）
    tools: list[Any] = []
    tool_names: list[str] = []
    if tools_enabled:
        from app.agent.mcp.server import weather_tools
        from app.agent.tools.registry import build_tools

        tools.extend(build_tools())
        tools.extend(weather_tools())
        tool_names = [getattr(t, "name", str(t)) for t in tools]
        try:
            from app.services.mcp import wrap_enabled_tools

            mcp_tools = await wrap_enabled_tools(session)
            tools.extend(mcp_tools)
            tool_names.extend(getattr(t, "name", str(t)) for t in mcp_tools)
        except Exception as exc:  # 第三方 MCP 装配失败不阻断
            logger.warning("mcp_tools_attach_failed", error=str(exc))

    # 子代理（DB 配置：内置播种 + 用户自建；model_ref 预解析为 ChatModel 实例）
    # 会话级选择（subagent_names）非空时只装配选中者（手册「三、(3) 输入窗口」）
    subagents: list[Any] = []
    if subagents_enabled:
        specs = await load_subagent_specs(session)
        if subagent_names:
            specs = [s for s in specs if s["name"] in subagent_names]
        resolved = await resolve_subagent_models(session, {s["model_ref"] for s in specs if s.get("model_ref")})
        subagents = build_subagents_from_specs(specs, resolved)

    middleware_stack = build_middleware_stack(
        primary_model=model,
        fallback_models=fallback_models or [],
        recap_provider=recap_provider,
        write_approval_enabled=write_approval_enabled,
        model_call_limit=get_settings().agent_model_call_limit,
    )

    # 系统提示（项目元信息 + 铁律；前情提要经 RecapInjection 动态注入）
    novel = await _load_novel_meta(session, project_id)
    system_prompt = render(
        "main_agent.md.j2",
        novel_title=novel.get("title") or "未命名项目",
        author_name=novel.get("author_name"),
        genre=novel.get("genre"),
        synopsis=novel.get("synopsis"),
        project_id=project_id,
        recap="",
        extra_instructions="",
    )

    skills = list(SKILL_PATHS)
    user_skills = await enabled_user_skills(session)
    if user_skills:
        logger.info("user_skills_enabled", count=len(user_skills), names=user_skills[:10])
    if session_skill_count:
        skills.append(f"/skills/session-{session_id}/")
        logger.info("session_skills_attached", session_id=session_id, count=session_skill_count)

    from app.agent.loop.checkpointer import get_checkpointer

    checkpointer = await get_checkpointer()

    kwargs: dict[str, Any] = {
        "model": model,
        "tools": tools,
        "system_prompt": system_prompt,
        "backend": backend,
        "permissions": permissions,
        "middleware": middleware_stack,
        "skills": skills,
        "interrupt_on": dict(INTERRUPT_ON),
        "context_schema": AgentContext,
        "checkpointer": checkpointer,
        "name": f"writer-{project_id[:8]}",
    }
    if subagents:
        kwargs["subagents"] = subagents

    graph = create_deep_agent(**kwargs)

    logger.info(
        "agent_built",
        project_id=project_id,
        tools=tool_names,
        subagents=[s["name"] for s in subagents],
        middleware=[getattr(m, "name", type(m).__name__) for m in middleware_stack],
        recursion_limit=recursion_limit,
    )

    return {
        "graph": graph,
        "system_prompt": system_prompt,
        "tool_names": tool_names,
        "subagent_names": [s["name"] for s in subagents],
        "recursion_limit": recursion_limit,
        "config_version": current_config_version(),
    }


async def _load_novel_meta(session: AsyncSession, project_id: str) -> dict[str, Any]:
    from app.db.models import Novel

    novel = await session.get(Novel, project_id)
    if novel is None:
        return {}
    return {
        "title": novel.title,
        "author_name": novel.author_name,
        "genre": novel.genre,
        "synopsis": novel.synopsis,
    }
