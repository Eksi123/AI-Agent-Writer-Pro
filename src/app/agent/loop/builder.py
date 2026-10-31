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

from deepagents import create_deep_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    TodoListMiddleware,
)
from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm.factory import get_chat_model
from app.agent.loop.checkpointer import get_checkpointer
from app.agent.loop.context import AgentContext
from app.agent.loop.middleware import (
    FileChangeNotifyMiddleware,
    RecapInjectionMiddleware,
    SteerInjectionMiddleware,
    WorkspaceGuardMiddleware,
    WriteApprovalMiddleware,
)
from app.agent.prompts import render
from app.agent.sandbox import build_backend, build_permissions
from app.agent.subagents import BUILTIN_SUBAGENTS
from app.core.config import current_config_version, get_settings
from app.core.logging import get_logger
from app.db.models import Novel, SkillConfig, SubagentConfig

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
                "tools": [],
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
                #: v1028：多选的内置工具名（read_file/edit_file/ls/grep/glob/web_search）
                "tools": _loads_list(row.tools_json),
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


#: v1028：子代理可选的内置工具。键为 UI 展示名（read/edit/list/grep/glob/web_search），
#: 值为 deepagents FilesystemMiddleware 的工具名（web_search 走自研 registry，不在 fs 工具内）。
_FS_TOOL_ALIASES = {
    "read_file": "read_file",
    "read": "read_file",
    "edit_file": "edit_file",
    "edit": "edit_file",
    "ls": "ls",
    "list": "ls",
    "grep": "grep",
    "glob": "glob",
    "web_search": "web_search",
    "web_serach": "web_search",  # 容错：文档里的拼写
}


def build_subagents_from_specs(
    specs: list[dict[str, Any]],
    resolved_models: dict[str, BaseChatModel],
    *,
    backend: Any = None,
    permissions: Any = None,
) -> list[Any]:
    """把子代理 spec 列表装配为 deepagents `SubAgent` 列表（model_ref 预解析为 ChatModel 实例）。

    v1028：`spec["tools"]` 为勾选的内置工具名（read/edit/list/grep/glob/web_search）。
    非空时把子代理的工具集收窄为这些工具——文件类工具经
    `FilesystemMiddleware(tools=[...])` 限定，`web_search` 直接给工具实例。
    """
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

        selected = _resolve_selected_tools(spec.get("tools") or [])
        fs_tools = selected["fs"]
        extra_tools = selected["extra"]
        if selected["selected"]:
            # 显式勾选 → 收窄工具集：文件类工具经 FilesystemMiddleware(tools=[...]) 限定
            # （deepagents 按中间件 name 原位替换基座 FilesystemMiddleware）。
            # read_file 是 deepagents 强制要求的最小集。
            agent["tools"] = list(extra_tools)
            if backend is not None:
                try:
                    from deepagents.middleware.filesystem import FilesystemMiddleware

                    restricted = list(fs_tools) or ["read_file"]
                    agent["middleware"] = [
                        FilesystemMiddleware(backend=backend, tools=restricted, _permissions=permissions)
                    ]
                except Exception as exc:  # pragma: no cover - deepagents 版本差异兜底
                    logger.warning("subagent_fs_middleware_failed", name=spec["name"], error=str(exc))

        ref = spec.get("model_ref")
        if ref:
            chat_model = resolved_models.get(ref)
            if chat_model is not None:
                agent["model"] = chat_model
            else:
                logger.warning("subagent_model_unresolved", name=spec["name"], model_ref=ref)
        result.append(agent)
    return result


def _resolve_selected_tools(names: list[str]) -> dict[str, Any]:
    """把勾选的工具名解析为「文件工具名列表 + 非文件工具实例列表 + 是否有显式勾选」。

    ★deepagents 要求 FilesystemMiddleware 的 tools 必含 `read_file`，故勾选了任何
    文件工具时自动补上 read_file。
    """
    fs: list[str] = []
    want_web_search = False
    selected = False
    for raw in names:
        key = (raw or "").strip()
        if not key:
            continue
        real = _FS_TOOL_ALIASES.get(key)
        if real is None:
            logger.warning("subagent_tool_unknown", tool=raw)
            continue
        selected = True
        if real == "web_search":
            want_web_search = True
        elif real not in fs:
            fs.append(real)

    if fs and "read_file" not in fs:
        fs.insert(0, "read_file")

    extra: list[Any] = []
    if want_web_search:
        try:
            from app.agent.tools.web_search import web_search

            extra.append(web_search)
        except Exception as exc:  # pragma: no cover
            logger.warning("web_search_tool_unavailable", error=str(exc))
    return {"fs": fs, "extra": extra, "selected": selected}


async def resolve_subagent_models(
    session: AsyncSession, model_refs: set[str]
) -> dict[str, BaseChatModel]:
    """把子代理需要的 model_ref 预解析为 ChatModel 实例（`SubAgent.model` 只接受实例）。"""

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
    steer_provider: Any = None,
    model_call_limit: int = DEFAULT_MODEL_CALL_LIMIT,
    write_approval_enabled: bool = True,
    max_context: int | None = None,
) -> list[Any]:
    """按手册顺序装配中间件栈。

    顺序（不可随意调换）：
    ModelRetry → ModelFallback → ModelCallLimit → TodoList
    → RecapInjection → SteerInjection → WorkspaceGuard → FileChangeNotify → WriteApproval
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
    stack.append(SteerInjectionMiddleware(steer_provider=steer_provider))
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


def _steer_provider(run_id: str) -> list[str]:
    """v1028：取出该 run 的用户引导消息（「通过消息引导发送」）。

    经 `run_manager` 的 run 级队列中转；builder 不持有状态，避免并发 run 串数据。
    """
    from app.agent.loop.runner import run_manager

    return run_manager.take_steer(run_id)


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
        subagents = build_subagents_from_specs(specs, resolved, backend=backend, permissions=permissions)

    middleware_stack = build_middleware_stack(
        primary_model=model,
        fallback_models=fallback_models or [],
        recap_provider=recap_provider,
        steer_provider=_steer_provider,
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

    novel = await session.get(Novel, project_id)
    if novel is None:
        return {}
    return {
        "title": novel.title,
        "author_name": novel.author_name,
        "genre": novel.genre,
        "synopsis": novel.synopsis,
    }
