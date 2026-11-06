# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.builder
文件名: builder.py
作者: xionggaoqing
时间: 2026-10-27
说明: 内置子代理 → deepagents `SubAgent` 装配（手册「二、(3) Sub-agents」）。

    （架构优化）：
    - 内置定义改为 `builtin/{name}/spec.py + prompt.md`（提示词独立文件），
      由 `builtin/__init__.py` 聚合为 `BUILTIN_SUBAGENTS`；
    - spec 新增 `label`（UI 展示名）与 `workflows`（固定流程绑定，见 agent/workflows）；
    - **内置优先**：用户自建子代理与内置同名时，DB 唯一约束直接挡住；功能重叠由
      UI 红字提示 + 装配顺序（内置在前）保证优先。

    每个子代理可配置模型覆盖（model_ref）、专属提示词、工具白名单、可用技能、
    上下文模式（mode，默认 isolated；fork 禁用 skills）。
    `general-purpose` 由 deepagents 在传入 subagents 时自动追加。

    派发语义（手册「二、(3)」，主 Agent ↔ 子 Agent）：
    主 Agent 统筹决策 → 经 `task` 工具派发子 Agent 任务 → 子 Agent 独立执行
    （mode=isolated，不继承主 Agent 上下文）→ 结果回传主 Agent 汇总。
    技能渐进披露：子 Agent 只在需要时自己 `read_file` 读 SKILL.md。
"""

from __future__ import annotations

from typing import Any

from deepagents import SubAgent

from app.agent.subagents.builtin import BUILTIN_SUBAGENTS
from app.agent.subagents.scrub import SubagentContextScrubMiddleware
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_subagents(
    *,
    resolved_models: dict[str, Any] | None = None,
    enabled_names: set[str] | None = None,
    model_overrides: dict[str, str] | None = None,
    prompt_overrides: dict[str, str] | None = None,
    default_model: Any | None = None,
) -> list[SubAgent]:
    """装配内置子代理为 `SubAgent` 列表。

    Args:
        resolved_models: `model_ref → BaseChatModel` 实例（由 builder 异步预解析后传入）。
            `SubAgent.model` 若传字符串会被 deepagents 按 `provider:model-name` 解析，
            而本项目 model_ref 是 `{provider_id}:{model_id}`（uuid:model_id）无法解析，
            故必须传 ChatModel 实例。
        enabled_names: 只装配这些子代理（None = 全部）。
        model_overrides: name → model_ref（子代理模型覆盖）。
        prompt_overrides: name → system_prompt（DB 自定义提示词覆盖内置）。
        default_model: 未配模型覆盖时回落主模型（create_sub_agent 要求必带 model）。
    """
    models = resolved_models or {}
    overrides = model_overrides or {}
    prompt_ovr = prompt_overrides or {}
    result: list[SubAgent] = []

    for name, spec in BUILTIN_SUBAGENTS.items():
        if enabled_names is not None and name not in enabled_names:
            continue

        mode = spec.get("mode", "isolated")
        skills = list(spec.get("skills") or [])
        if mode == "fork" and skills:
            # fork 下设 skills 会抛错（实测），清空并告警
            logger.warning("subagent_fork_skills_cleared", name=name)
            skills = []

        agent: dict[str, Any] = {
            "name": name,
            "description": spec["description"],
            "system_prompt": prompt_ovr.get(name) or spec["system_prompt"],
            "mode": mode,
        }
        if skills:
            agent["skills"] = skills
        # 子代理用完即销毁：结束时清空中间上下文，回传主图只留最终结论
        agent["middleware"] = [SubagentContextScrubMiddleware()]

        model_ref = overrides.get(name)
        chat_model = models.get(model_ref) if model_ref else None
        if model_ref and chat_model is None:
            logger.warning("subagent_model_unresolved", name=name, model_ref=model_ref)
        agent["model"] = chat_model or default_model

        result.append(agent)  # type: ignore[arg-type]

    return result


def workflows_of(name: str) -> list[str]:
    """子代理绑定的固定流程名（agent/workflows 下的流程；空列表 = 不绑定）。"""
    spec = BUILTIN_SUBAGENTS.get(name) or {}
    return list(spec.get("workflows") or [])
