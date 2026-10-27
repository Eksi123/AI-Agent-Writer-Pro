# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.builder
文件名: builder.py
作者: xionggaoqing
时间: 2026-10-27
说明: 内置子代理 → deepagents `SubAgent` 装配（手册「二、(3) Sub-agents」）。

    每个子代理可配置模型覆盖（model_ref）、专属提示词、可用技能、上下文模式
    （mode，默认 isolated；fork 禁用 skills）。`general-purpose` 由 deepagents
    在传入 subagents 时自动追加。
"""

from __future__ import annotations

from typing import Any

from deepagents import SubAgent

from app.agent.subagents.builtin import BUILTIN_SUBAGENTS
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_subagents(
    *,
    resolved_models: dict[str, Any] | None = None,
    enabled_names: set[str] | None = None,
    model_overrides: dict[str, str] | None = None,
    prompt_overrides: dict[str, str] | None = None,
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

        model_ref = overrides.get(name)
        if model_ref:
            chat_model = models.get(model_ref)
            if chat_model is not None:
                agent["model"] = chat_model
            else:
                logger.warning("subagent_model_unresolved", name=name, model_ref=model_ref)

        result.append(agent)  # type: ignore[arg-type]

    return result
