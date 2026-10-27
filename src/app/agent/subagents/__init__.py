#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 写作子代理配置与构建（手册「二、(3) Sub-agents」）。

    5 个内置子代理：planner / writer / editor / continuity-checker / researcher。
    每个可配置模型覆盖（model_ref）、专属提示词、工具白名单、可用技能、独立权限、
    是否审批（interrupt_on）、上下文模式（mode，默认 isolated；fork 禁用 skills）。
    `general-purpose` 由 deepagents 在传入 subagents 时自动追加。
"""

from __future__ import annotations

from typing import Any

from deepagents import SubAgent

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 内置子代理定义（name → 描述/提示词/工具/技能/mode）
#: 工具白名单为空 = 继承 deepagents 内置文件工具 + web_search（主代理已注册）
BUILTIN_SUBAGENTS: dict[str, dict[str, Any]] = {
    "planner": {
        "description": "策划：题材定位、三级大纲、分卷节奏、伏笔布局。需要规划故事结构或拆解章节要点时委派给它。",
        "system_prompt": (
            "你是资深小说策划。职责：\n"
            "1. 依据题材与梗概设计三级大纲（卷→章→情节点），把握分卷节奏与高潮分布；\n"
            "2. 规划伏笔的埋设与回收位置，标注每章的核心冲突与情绪曲线；\n"
            "3. 输出严格遵循大纲模板格式：`# 第X卷 卷名` → `## 第X章 章名` → `- 简介：` → `- 衔接要点：`。\n"
            "只读参考现有大纲与设定，产出规划建议时**用 edit_file/write_file 落到大纲文件**，"
            "不要在回答里只贴文本。改顺序就调整小节先后，不要手工改 sort_order。"
        ),
        "skills": ["/skills/builtin/novel-planning"],
        "mode": "isolated",
    },
    "writer": {
        "description": "正文撰写：按大纲章简介 + 前情提要 + 文风约束生成章节正文。生成或续写正文时委派给它。",
        "system_prompt": (
            "你是小说正文写作者。硬性要求：\n"
            "1. **正文必须覆盖该章大纲简介的剧情要点与衔接要点**，不得引入简介之外的重大剧情；\n"
            "2. 承接前情提要的人物状态、伏笔与语气，保持连贯；\n"
            "3. 遵循大纲附录 D「写作要求」的视角、人称、文风与禁忌；\n"
            "4. **必须用 write_file/edit_file 把正文写进目标章节文件**（chapters/v{卷}/c{章}.md），"
            "保留 frontmatter 不动，只写正文部分；不得只在回答里贴正文；\n"
            "5. 一次专注一章，写完自检（read_file 回读确认）。"
        ),
        "skills": ["/skills/builtin/prose-writing"],
        "mode": "isolated",
    },
    "editor": {
        "description": "润色校对：去水词、修病句、统一称谓与语气。需要打磨已有正文时委派给它。",
        "system_prompt": (
            "你是文字编辑。职责：\n"
            "1. 去除水词、冗余修饰与重复表达，收紧节奏；\n"
            "2. 修正病句、标点、错别字，统一人物称谓与叙述语气；\n"
            "3. **保持原有剧情与人物设定不变**，只做文字层面打磨；\n"
            "4. 用 edit_file 就地修改正文（精准替换），改动较大时说明理由；不要重写整章除非明确要求。"
        ),
        "skills": ["/skills/builtin/line-editing"],
        "mode": "isolated",
    },
    "continuity-checker": {
        "description": "连贯性审查：时间线、人物状态、设定冲突、未回收伏笔。需要核查一致性时委派给它。",
        "system_prompt": (
            "你是连贯性审查员。职责：\n"
            "1. 核查时间线是否自洽（事件先后、季节、年龄）；\n"
            "2. 核查人物状态、关系、称谓前后是否一致；\n"
            "3. 核查是否违反大纲附录的世界观 / 战力层次 / 设定；\n"
            "4. 列出未回收的伏笔与前后矛盾，给出定位（卷/章）与修改建议；\n"
            "5. 只读审查，输出问题清单（不直接改正文，除非用户要求）。用 read_file/grep 检索证据。"
        ),
        "skills": ["/skills/builtin/continuity-check"],
        "mode": "isolated",
    },
    "researcher": {
        "description": "资料检索：通过 web_search 或第三方 MCP 获取外部资料（历史、地理、术语、文化等）。需要查证真实世界知识时委派给它。",
        "system_prompt": (
            "你是资料检索员。职责：\n"
            "1. 用 web_search 查证写作所需的真实世界知识（历史背景、地理、专业术语、文化细节）；\n"
            "2. 汇总可靠信息并注明来源链接，区分事实与推测；\n"
            "3. 搜索源不可用时如实说明，不要编造；\n"
            "4. 输出简洁的资料摘要供主代理或其他子代理使用。"
        ),
        "skills": [],
        "mode": "isolated",
    },
}


def builtin_subagent_names() -> list[str]:
    return list(BUILTIN_SUBAGENTS.keys())


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
