# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.builder
文件名: builder.py
作者: xionggaoqing
时间: 2026-11-09
说明: `create_deep_agent` 装配（手册「二、(2) Agent-Loop」+「二、(5)」）。

    装配要点（架构优化后）：
    - `backend`：CompositeBackend 路由（`/projects/`、`/skills/user/`、会话精选）
      —— 技能 = 用户自建，无 `/skills/builtin/` 路由
    - `permissions`：8 条有序规则（`{pid}` 替换为当前项目），兜底 deny 最后
    - `checkpointer`：AsyncSqliteSaver（`data/agent_state.db`），按 thread_id 持久化
    - `context_schema`：AgentContext（project_id/session_id/run_id/... 经 Runtime.context 注入）
    - `middleware` 顺序（手册「二、(2)」）：
      WriteApproval（harness/approval，最外层）→ ModelCallBudget（调用预算：超限先总结再咨询）
      → 重试/连续失败切换（harness/llm/resilience）
      → TodoList → RecapInjection → HiddenContext → ContextGuard（两层保险第二层）
      → PairingRepair → WastefulLoopGuard（分级熔断）→ Steer → ReadScopeGuard
      → WorkspaceGuard（harness/guard）→ FileChangeNotify → ContentLock（harness/lock）
    - `skills`：`["/skills/user/"]`（虚拟路径）+ 会话精选 `/skills/session-{sid}/`
    - `subagents`：3 个内置执行类子代理 writer/reviewer/recaller
      （subagents/builtin/*/spec.py + prompt.md；general-purpose 由 deepagents 自动追加）
      - 无预设降级链——当前模型连续失败达阈值才切换备用模型
      （ModelSwitchOnFailureMiddleware）；删除卷章弹风险确认，编辑/生成不审批
      - `interrupt_on` 置空——删除卷章的风险确认由 WriteApprovalMiddleware
      按参数判定（批准 / 拒绝 / 补充要求 三选一）

    **不引入** LocalShellBackend / LangSmithSandbox（会激活 execute 且与 permissions 冲突）。
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.harness.llm.call_budget import ModelCallBudgetMiddleware
from app.agent.harness.llm.prompt_cache import PromptCacheMiddleware
from app.agent.harness.llm.resilience import build_resilience_middleware
from app.agent.harness.llm.wasteful_loop import WastefulLoopGuardMiddleware
from app.agent.harness.sandbox import build_backend, build_permissions
from app.agent.harness.sandbox.safe_delete import SafeDeleteMiddleware
from app.agent.loop.checkpointer import get_checkpointer
from app.agent.loop.context import AgentContext
from app.agent.loop.llm.factory import get_chat_model
from app.agent.loop.middleware import (
    ContentLockMiddleware,
    ContextOverflowGuardMiddleware,
    ContextWindowSyncMiddleware,
    FileChangeNotifyMiddleware,
    HiddenContextInjectionMiddleware,
    PairingRepairMiddleware,
    RecapInjectionMiddleware,
    SteerInjectionMiddleware,
    WorkspaceGuardMiddleware,
    ReadScopeGuardMiddleware,
    WriteApprovalMiddleware,
)
from app.agent.loop.middleware.context_guard import ContextGuardMiddleware
from app.agent.loop.middleware.recap import injected_slots_of
from app.agent.loop.prompts import render
from app.agent.subagents.scrub import SubagentContextScrubMiddleware
from app.agent.subagents.user import load_specs as load_user_specs
from app.core.config import current_config_version, get_settings
from app.core.logging import get_logger
from app.db.models import Novel, SkillConfig

logger = get_logger(__name__)

#: skills 虚拟路径（技能 = 用户自建；会话精选目录由 build_agent 按需追加）
SKILL_PATHS = ["/skills/user/"]

#: 不再用工具级 `interrupt_on`（它只能整工具开关且默认四按钮）；
#: 删除卷章的风险确认统一由 WriteApprovalMiddleware 按参数判定（批准/拒绝/补充要求）。
INTERRUPT_ON: dict[str, Any] = {}

#: 单次 run 的 LLM 调用软上限（硬上限另经 config recursion_limit）。
#: 40 → 60：整篇大纲分卷写入等长任务 40 次不够（实测 28 次调用 + 收尾守护已逼近），
#: 浪费型循环另有 WastefulLoopGuard 熔断，不靠压低上限兜底。
DEFAULT_MODEL_CALL_LIMIT = 60

#: 系统提示里梗概的注入上限（每轮模型调用都重发，长梗概改由 AI 按需读 novel.yaml）
_SYNOPSIS_LIMIT = 120


async def load_subagent_specs(session: AsyncSession) -> list[dict[str, Any]]:
    """从 DB 读取启用的子代理配置（内置已播种 + 用户自建）。

    装载逻辑迁至 `subagents/user.load_specs`（内置在前 = 内置优先），
    此处保留同名入口（builder/测试的既有调用点不变）。
    DB 为空（如未播种的测试环境）时回落内置定义，保证 agent 始终有写作子代理可用。
    """
    return await load_user_specs(session)


#: 子代理可选的内置工具。键为 UI 展示名（read/write/edit/list/grep/glob/web_search），
#: 值为 deepagents FilesystemMiddleware 的工具名；`web_search` / `outline_grep` /
#: `ask_user` / 结构工具走自研 registry（非文件工具，作为 extra 工具实例下发）。
_FS_TOOL_ALIASES = {
    "read_file": "read_file",
    "read": "read_file",
    "edit_file": "edit_file",
    "edit": "edit_file",
    "write_file": "write_file",
    "write": "write_file",
    "ls": "ls",
    "list": "ls",
    "grep": "grep",
    "glob": "glob",
    "web_search": "web_search",
    "web_serach": "web_search",  # 容错：文档里的拼写
    "outline_recall": "outline_grep",  # 兼容旧名（历史子代理配置/提示词残留）
    "outline_grep": "outline_grep",
    "ask_user": "ask_user",
    # 结构工具（create/rename/delete volume/chapter）
    "create_volume": "create_volume",
    "create_chapter": "create_chapter",
    "rename_volume": "rename_volume",
    "rename_chapter": "rename_chapter",
    "delete_volume": "delete_volume",
    "delete_chapter": "delete_chapter",
}

#: 非文件类自研工具（名字 → 惰性导入路径）
_EXTRA_TOOL_MODULES = {
    "web_search": "app.agent.tools.builtin.web_search",
    "outline_grep": "app.agent.tools.builtin.outline_grep",
    "ask_user": "app.agent.tools.builtin.ask_user",
    "create_volume": "app.agent.tools.builtin.create_structure",
    "create_chapter": "app.agent.tools.builtin.create_structure",
    "rename_volume": "app.agent.tools.builtin.create_structure",
    "rename_chapter": "app.agent.tools.builtin.create_structure",
    "delete_volume": "app.agent.tools.builtin.create_structure",
    "delete_chapter": "app.agent.tools.builtin.create_structure",
}


def build_subagents_from_specs(
    specs: list[dict[str, Any]],
    resolved_models: dict[str, BaseChatModel],
    *,
    backend: Any = None,
    permissions: Any = None,
    default_model: BaseChatModel | None = None,
    subagent_call_limit: int = 20,
    write_approval_enabled: bool = True,
) -> list[Any]:
    """把子代理 spec 列表装配为 deepagents `SubAgent` 列表（model_ref 预解析为 ChatModel 实例）。

    `spec["tools"]` 为勾选的内置工具名（read/edit/list/grep/glob/web_search）。
    非空时把子代理的工具集收窄为这些工具——文件类工具经
    `FilesystemMiddleware(tools=[...])` 限定，`web_search` 直接给工具实例。

    `default_model`：子代理未配 model_ref（或解析失败）时回落主模型——
    deepagents `create_sub_agent` 要求 spec 必带 `model`，缺失会抛 ValueError。
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
        #: 子代理用完即销毁：结束时清空中间上下文，回传主图只留最终结论
        middleware: list[Any] = [SubagentContextScrubMiddleware()]
        # 子代理一次委派同样受调用预算约束：超限先总结再 consult 询问继续/结束
        # （isolated 子图的 interrupt 会冒泡到主图，前端同一张咨询卡片）
        middleware.append(
            ModelCallBudgetMiddleware(
                limit=subagent_call_limit,
                summary_model=default_model,
                steer_sink=_push_steer,
            )
        )
        # ★ 防护/通知中间件必须同样挂到子代理栈上，且要与主栈**同构**。
        #
        # 提示词铁律要求「正文一律委派 writer 子代理」（main_agent.md.j2:14-18、
        # workflow_generate.md.j2:21），也就是说**正文生成的主路径在子代理里**。
        # 而 deepagents 只按中间件 name 把主栈中间件继承给子代理的基座槽位，
        # 自建的这几个都不在其中 → 曾经出现：writer 写完章节文件后
        #   ① 不发 `file.changed`（前端热更新链断、目录树/索引/FTS 全不同步）；
        #   ② 生成期间该章无「AI 编辑锁」（用户并发保存与 AI 整文件覆盖互相吃掉）；
        #   ③ 文件读写删零审计、零路径校验、零 tool.start/tool.result 事件；
        #   ④ `delete` 是 unlink/rmtree 永久删除，不进回收站；
        #   ⑤ **删除不弹审批卡**：`INTERRUPT_ON` 是空字典（见本文件 :80），框架层
        #      不拦任何工具，删除审批的唯一来源是 `WriteApprovalMiddleware`；
        #      子代理栈上没有它，writer 就能静默删章。
        #   ⑥ 子代理可整篇读大纲而不触发授权卡（`ReadScopeGuardMiddleware` 同理）。
        #
        # ★ 为什么「主↔子上下文隔离」不妨碍这些中间件生效：隔离切断的只是**消息**
        #   （子代理的中间步骤不回传主图，收尾还会被 SubagentContextScrub 清空），
        #   而这些中间件依赖的三样东西都**不在消息里**——
        #     · `runtime.context`（run_id / session_id / emit）：同一次 graph 调用内
        #       主图与 isolated 子图共享同一个 context 对象，emit 指向同一个按
        #       run_id 寻址的 SSE 总线，所以子代理发的事件前端照样收得到；
        #     · `content_locks`：进程级单例，按 run_id 加解锁，run 收尾统一释放，
        #       与「哪个栈加的锁」无关；
        #     · DB / 文件系统：各自 `session_scope()` 直连，不经对话上下文。
        #   反过来，**依赖 interrupt() 的闸门**（删除审批、读大纲授权）之所以也能跨
        #   隔离生效，是因为 LangGraph 的 GraphInterrupt 会从 isolated 子图**冒泡**
        #   到主图（同 ModelCallBudget 的既有注释），前端弹的是同一张卡片。
        #
        # 相对顺序须与主栈一致：WriteApproval 最外（其 interrupt 不得被吞），
        # ReadScope → WorkspaceGuard → FileChangeNotify → SafeDelete → ContentLock
        # （FileChangeNotify 在 SafeDelete 外层：后者不调 handler、自行返回成功结果，
        #  前者要靠该结果发 file.changed）。
        middleware.append(WriteApprovalMiddleware(enabled=write_approval_enabled))
        middleware.append(ReadScopeGuardMiddleware())
        middleware.append(WorkspaceGuardMiddleware())
        middleware.append(FileChangeNotifyMiddleware())
        middleware.append(SafeDeleteMiddleware())
        middleware.append(ContentLockMiddleware())
        if selected["selected"]:
            # 显式勾选 → 收窄工具集：文件类工具经 FilesystemMiddleware(tools=[...]) 限定
            # （deepagents 按中间件 name 原位替换基座 FilesystemMiddleware）。
            # read_file 是 deepagents 强制要求的最小集。
            agent["tools"] = list(extra_tools)
            if backend is not None:
                try:
                    from deepagents.middleware.filesystem import FilesystemMiddleware

                    restricted = list(fs_tools) or ["read_file"]
                    middleware.append(
                        FilesystemMiddleware(backend=backend, tools=restricted, _permissions=permissions)
                    )
                except Exception as exc:  # pragma: no cover - deepagents 版本差异兜底
                    logger.warning("subagent_fs_middleware_failed", name=spec["name"], error=str(exc))
        agent["middleware"] = middleware

        ref = spec.get("model_ref")
        chat_model = resolved_models.get(ref) if ref else None
        if ref and chat_model is None:
            logger.warning("subagent_model_unresolved", name=spec["name"], model_ref=ref)
        # 无 model_ref / 解析失败 → 回落主模型（create_sub_agent 要求 spec 必带 model）
        agent["model"] = chat_model or default_model
        result.append(agent)
    return result


def _resolve_selected_tools(names: list[str]) -> dict[str, Any]:
    """把勾选的工具名解析为「文件工具名列表 + 非文件工具实例列表 + 是否有显式勾选」。

    deepagents 要求 FilesystemMiddleware 的 tools 必含 `read_file`，故勾选了任何
    文件工具时自动补上 read_file。非文件工具（`web_search` / `outline_grep`）
    直接作为工具实例下发。
    """
    fs: list[str] = []
    want_extra: list[str] = []
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
        if real in _EXTRA_TOOL_MODULES:
            if real not in want_extra:
                want_extra.append(real)
        elif real not in fs:
            fs.append(real)

    if fs and "read_file" not in fs:
        fs.insert(0, "read_file")

    extra: list[Any] = []
    for name in want_extra:
        try:
            import importlib

            mod = importlib.import_module(_EXTRA_TOOL_MODULES[name])
            extra.append(getattr(mod, name))
        except Exception as exc:  # pragma: no cover
            logger.warning("subagent_extra_tool_unavailable", tool=name, error=str(exc))
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
    alternate_models: list[BaseChatModel],
    recap_provider: Any = None,
    outline_ctx_provider: Any = None,
    steer_provider: Any = None,
    hidden_context_provider: Any = None,
    model_call_limit: int = DEFAULT_MODEL_CALL_LIMIT,
    write_approval_enabled: bool = True,
    max_context: int | None = None,
) -> list[Any]:
    """按手册顺序装配中间件栈。

    顺序（不可随意调换）：
    WriteApproval → ModelCallBudget（调用预算：超限先总结再咨询继续/结束）
    → ModelRetry → ModelSwitchOnFailure
    → TodoList → RecapInjection → HiddenContext → ContextGuard（两层保险第二层）
    → PairingRepair → WastefulLoopGuard（分级熔断）→ SteerInjection → ReadScopeGuard
    → WorkspaceGuard → FileChangeNotify → ContentLock
    （压缩 Summarization 与窗口同步 ContextWindowSync 在 RecapInjection 之前，
    见装配代码⑤：窗口同步须紧随压缩中间件）

    删除 ModelFallback 降级链——改为「当前模型连续失败达阈值才切换备用模型」
    （ModelSwitchOnFailureMiddleware）；编辑大纲/生成正文不审批，仅删除卷章弹风险确认。

    WriteApproval 必须最外层——其 `interrupt()` 抛出的 GraphInterrupt 若被
    外层中间件（deepagents Filesystem 等）的 try/except 吞掉，会变成一条错误 ToolMessage，
    审批永远无法暂停 run（resolve 后 resume 空转、Agent 反复重试）。置于最外层后
    GraphInterrupt 直达 ToolNode，正确挂起 graph。
    """
    stack: list[Any] = []

    # ⓪ 高危写入审批（最外层：保证 interrupt() 的 GraphInterrupt 不被吞）
    stack.append(WriteApprovalMiddleware(enabled=write_approval_enabled))

    # ⓪.5 调用预算（外层：其 interrupt() 同样不得被 Retry/Switch 吞掉）：
    # 即将超限时先总结已处理结果，再 consult 弹窗询问「继续思考 / 结束并输出总结」
    stack.append(
        ModelCallBudgetMiddleware(
            limit=model_call_limit,
            summary_model=primary_model,
            steer_sink=_push_steer,
        )
    )

    # ①~② LLM 容错两段（重试 → 连续失败切换）：装配逻辑在 harness/llm/resilience
    stack.extend(
        build_resilience_middleware(
            primary_model=primary_model,
            alternate_models=alternate_models,
        )
    )

    # ③.5 上下文超限兜底：设定窗口 > 真实窗口时 provider 报超限错误，
    # 由该中间件捕获并主动摘要压缩历史后重试（挂在压缩中间件外层）
    stack.append(ContextOverflowGuardMiddleware(primary_model))

    # ④ 待办：提供 write_todos 工具 → plan.update 事件（deepagents 默认不带，显式加入）
    try:
        stack.append(TodoListMiddleware())
    except Exception as exc:  # pragma: no cover
        logger.warning("todo_list_mw_unavailable", error=str(exc))

    # ⑤ 上下文压缩：超阈值摘要（deepagents 版在 deepagents.middleware.summarization）
    summarization_mw = _build_summarization(primary_model, max_context=max_context)
    if summarization_mw is not None:
        # 窗口配置实时同步须排在压缩**之前**（before_model 按栈序执行）：
        # 装配后用户改小 Context Length 时，下一次模型调用先重算阈值再判定压缩，
        # 保证「比例已超即先压缩」不因旧阈值失效
        stack.append(ContextWindowSyncMiddleware(summarization_mw))
        stack.append(summarization_mw)

    # ⑥~⑩ 自定义中间件
    stack.append(
        RecapInjectionMiddleware(
            recap_provider=recap_provider,
            outline_ctx_provider=outline_ctx_provider,
        )
    )
    stack.append(HiddenContextInjectionMiddleware(provider=hidden_context_provider))
    # 两层保险第二层：确定性 provider 全未命中但用户消息含明确「回顾/按大纲生成」
    # 指令时，提示模型自行获取上下文；无明确指令则不注入、禁止自作主张
    stack.append(ContextGuardMiddleware(injected_slots_provider=injected_slots_of))
    # 工具消息配对自愈：多并行 tool call 错位（结果丢失/重复成对）在模型调用前
    # 原地修复——错位上下文会诱导模型反复重读附件（token 暴涨的根因之一）
    stack.append(PairingRepairMiddleware())
    # 浪费型循环熔断：检测反复重读/重写后 push 纠正引导，须挂在 SteerInjection
    # **之前**——before_model 钩子按栈序执行，同一次模型调用即可带上纠正消息
    stack.append(WastefulLoopGuardMiddleware(steer_sink=_push_steer))
    stack.append(SteerInjectionMiddleware(steer_provider=steer_provider))
    # 读取授权守卫在 WorkspaceGuard 之前：先判定是否允许读，再走路径校验/审计
    stack.append(ReadScopeGuardMiddleware())
    stack.append(WorkspaceGuardMiddleware())
    stack.append(FileChangeNotifyMiddleware())
    # 删除改为可恢复语义（章节走回收站、其它走 trash 留底），须在 FileChangeNotify
    # **内层**：它不调用 handler 而自行返回成功结果，外层要靠该结果发 file.changed
    stack.append(SafeDeleteMiddleware())
    stack.append(ContentLockMiddleware())
    # 最内层：所有注入完成后给稳定前缀打显式缓存断点（未打标记的模型原样透传）
    stack.append(PromptCacheMiddleware())

    return stack


#: deepagents 默认通用子代理的名字（提供同名 spec 即可阻止自动注入）
_GP_NAME = "general-purpose"

_GP_DISABLED_PROMPT = (
    "你已被停用：不要执行任何操作，不要调用任何工具。\n"
    "立即用一句话回复——「通用子代理已停用，请改用 writer（写正文）/ "
    "reviewer（审稿）/ recaller（查设定与前情）之一，并说明具体任务。」"
)


def _inert_general_purpose_spec(backend: Any, permissions: Any) -> dict[str, Any]:
    """顶替 deepagents 默认 general-purpose 子代理的**惰性壳**（等同停用）。

    - `tools=[]`：`graph.py:787` 是 `spec.get("tools") if "tools" in spec else tools`，
      显式给空列表即**不继承**主 Agent 工具——否则默认 GP 会拿到含 `delete` 的全部工具；
    - 文件工具经 `FilesystemMiddleware(tools=["read_file"])` 按 name 原位替换基座，只留只读；
    - 仍带上与自建子代理相同的防护/通知中间件，避免出现「零防护执行体」。
    """
    middleware: list[Any] = [
        SubagentContextScrubMiddleware(),
        # 与自建子代理同构的防护链。这个壳只有 read_file，删除/写入类闸门
        # （WriteApproval / SafeDelete / ContentLock）用不上，但读取授权与
        # 路径校验/审计仍必须在——否则它成了唯一能「零审计整篇读大纲」的入口。
        ReadScopeGuardMiddleware(),
        WorkspaceGuardMiddleware(),
        FileChangeNotifyMiddleware(),
        SafeDeleteMiddleware(),
        ContentLockMiddleware(),
    ]
    if backend is not None:
        try:
            from deepagents.middleware.filesystem import FilesystemMiddleware

            middleware.append(
                FilesystemMiddleware(backend=backend, tools=["read_file"], _permissions=permissions)
            )
        except Exception as exc:  # pragma: no cover - deepagents 版本差异兜底
            logger.warning("gp_fs_middleware_failed", error=str(exc))
    return {
        "name": _GP_NAME,
        "description": (
            "（已停用）通用子代理不可用。请改用 writer / reviewer / "
            "recaller 之一。"
        ),
        "system_prompt": _GP_DISABLED_PROMPT,
        "tools": [],
        "middleware": middleware,
    }


def _build_summarization(model: BaseChatModel, *, max_context: int | None) -> Any | None:
    """上下文压缩中间件（deepagents 版：卸载驱逐消息 + 预截断大参数 + 溢出重试）。

    触发阈值改为**配置窗口驱动**——已注入上下文剩余 <20%（即达到窗口 80%）
    时自动压缩；窗口未知时回落 deepagents 的模型画像默认值。
    """
    window = int(max_context or 0)
    try:
        from deepagents.middleware.summarization import (
            SummarizationMiddleware as DeepSummarizationMiddleware,
            compute_summarization_defaults,
            create_summarization_middleware,
        )

        if window > 0:
            defaults = compute_summarization_defaults(model)
            return DeepSummarizationMiddleware(
                model=model,
                backend=build_backend(),
                # 剩余 <20% 即压缩：触发点 = 窗口 × 80%
                trigger=("tokens", max(2000, int(window * 0.8))),
                keep=defaults["keep"],
                truncate_args_settings=defaults["truncate_args_settings"],
            )
        return create_summarization_middleware(model, build_backend())
    except Exception as exc:
        logger.info("summarization_mw_unavailable", error=str(exc))
    # 回落 langchain 版
    try:
        from langchain.agents.middleware import SummarizationMiddleware

        tokens = int((window or 262_144) * 0.8)
        return SummarizationMiddleware(model=model, trigger=("tokens", max(2000, tokens)))
    except Exception as exc:  # pragma: no cover
        logger.warning("summarization_fallback_unavailable", error=str(exc))
        return None


def _steer_provider(run_id: str) -> list[str]:
    """取出该 run 的用户引导消息（「通过消息引导发送」）。

    经 `run_manager` 的 run 级队列中转；builder 不持有状态，避免并发 run 串数据。
    """
    from app.agent.loop.runner import run_manager

    return run_manager.take_steer(run_id)


def _push_steer(run_id: str, text: str) -> None:
    """向该 run 的引导队列入队一条系统纠正消息（浪费型循环熔断用）。"""
    from app.agent.loop.runner import run_manager

    run_manager.push_steer(run_id, text)


async def build_agent(
    session: AsyncSession,
    *,
    project_id: str,
    model: BaseChatModel,
    fallback_models: list[BaseChatModel] | None = None, # 语义=连续失败切换的备用模型
    recap_provider: Any = None,
    outline_ctx_provider: Any = None,
    subagents_enabled: bool = True,
    tools_enabled: bool = True,
    write_approval_enabled: bool = True,
    recursion_limit: int = 60,
    session_id: str = "",
    subagent_names: set[str] | None = None,
    skill_names: set[str] | None = None,
    hidden_context_provider: Any = None,
    max_context: int | None = None,
) -> dict[str, Any]:
    """构建 deep agent。返回装配结果 dict（含 graph 与元信息，供 runner 使用）。

    调用方传入已解析的 `model`（主模型）与可选 `fallback_models`（语义改为
    「连续失败切换」的备用模型列表）。
    `subagent_names` / `skill_names`：会话级选择（手册「三、(3) 输入窗口」上下文 Chips），
    空/None = 用全局默认（全部启用子代理 + 全部技能目录）。`session_id` 用于会话技能精选目录挂载。
    `max_context`：配置上下文窗口（token），驱动压缩中间件触发阈值（剩余 <10% 压缩）。
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
        from app.agent.more.mcp.server import weather_tools
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
        subagents = build_subagents_from_specs(
            specs,
            resolved,
            backend=backend,
            permissions=permissions,
            default_model=model,
            # 删除审批开关必须与主栈同源：否则「关掉审批」只关掉了主 Agent 的，
            # 子代理那边照旧弹卡（或反过来，主栈弹、子代理静默删）
            write_approval_enabled=write_approval_enabled,
        )

    middleware_stack = build_middleware_stack(
        primary_model=model,
        alternate_models=fallback_models or [],
        recap_provider=recap_provider,
        outline_ctx_provider=outline_ctx_provider,
        steer_provider=_steer_provider,
        hidden_context_provider=hidden_context_provider,
        write_approval_enabled=write_approval_enabled,
        model_call_limit=get_settings().agent_model_call_limit,
        max_context=max_context,
    )

    # 系统提示（项目元信息 + 铁律；前情提要/大纲摘录经 RecapInjection 按需注入）
    novel = await _load_novel_meta(session, project_id)
    # 梗概每次模型调用都重发 → 截断到 120 字（完整梗概在 novel.yaml，AI 可按需读）
    synopsis = _clip(str(novel.get("synopsis") or ""), _SYNOPSIS_LIMIT)
    system_prompt = render(
        "main_agent.md.j2",
        novel_title=novel.get("title") or "未命名项目",
        author_name=novel.get("author_name"),
        genre=novel.get("genre"),
        synopsis=synopsis,
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
    # ★ 始终显式提供 `general-purpose` spec。
    #
    # deepagents 在「调用方未提供同名 spec」时会自动注入一个默认通用子代理，
    # 其 `tools` = **主 Agent 全部工具（含 delete）**、且不携带本仓任何防护中间件
    # （graph.py:817-886）。而出厂 `subagents/builtin/` 里没有它 → 「子代理管理」
    # 界面既看不到也停不掉，用户把子代理**全部停选**时 `subagents` 为空列表，
    # 旧代码 `if subagents:` 干脆不传该键 → GP 照样被注入。
    #
    # 0.7.23 只在 harness profile 级提供 `GeneralPurposeSubagentProfile(enabled=False)`
    # 开关，而 profile 是按 `provider:identifier` 从全局注册表解析的
    # （graph.py:610,631），覆盖它需要注册自定义 profile 并丢掉内置 profile 的其余
    # 设定，跨版本脆弱。故改用**文档明确支持的稳定契约**：提供同名 spec 即不自动
    # 注入（graph.py:459-461），且显式 `tools=[]` 不会继承主工具（graph.py:787）。
    if not any(s.get("name") == _GP_NAME for s in subagents):
        subagents = [*subagents, _inert_general_purpose_spec(backend, permissions)]
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


def _clip(text: str, limit: int) -> str:
    """按字数截断（超长加省略号），用于系统提示里的长字段。"""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[:limit] + "…"
