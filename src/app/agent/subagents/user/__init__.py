# -*- coding: utf-8 -*-
"""
模块: app.agent.subagents.user
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 用户自建子代理装载。

    用户子代理的**配置数据**存 DB `subagent_configs`（is_builtin=False，
    经 services/subagents CRUD 维护）；本模块只负责把 DB 行装载为装配 spec
    （与内置 spec 同构），由 loop/builder 统一装配。

    与内置的优先级：同名被 DB 唯一约束挡住；功能重叠时内置优先——
    `load_specs` 把内置排在用户自建之前，主 Agent 的 task 工具按序展示，
    同域任务优先派发内置子代理。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.subagents.builtin import BUILTIN_SUBAGENTS
from app.core.logging import get_logger
from app.db.models import SubagentConfig

logger = get_logger(__name__)


def _loads_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def row_to_spec(row: SubagentConfig) -> dict[str, Any]:
    """DB 行 → 装配 spec（与内置 spec 同构；提示词为空时回落内置同名定义）。

    工具回落：DB 未显式勾选工具（tools_json 为空）时，用内置 spec 的默认工具集
    （按职能预配，见 builtin/{name}/spec.py）——保证内置子代理开箱即用。
    """
    mode = row.mode or "isolated"
    skills = _loads_list(row.skills_json)
    if mode == "fork" and skills:
        logger.warning("subagent_fork_skills_cleared", name=row.name)
        skills = []
    builtin = BUILTIN_SUBAGENTS.get(row.name, {})
    tools = _loads_list(row.tools_json)
    if not tools and row.is_builtin:
        tools = list(builtin.get("tools") or [])
    return {
        "name": row.name,
        "label": row.name,
        "description": row.description,
        "system_prompt": row.system_prompt or builtin.get("system_prompt", ""),
        "mode": mode,
        "skills": skills,
        "tools": tools,
        "workflows": [],
        "model_ref": row.model_ref,
        "is_builtin": bool(row.is_builtin),
    }


async def load_specs(session: AsyncSession) -> list[dict[str, Any]]:
    """读 DB 中启用的子代理行 → spec 列表（内置播种行在前、用户自建在后）。

    DB 为空（如未播种的测试环境）时回落内置定义，保证 agent 始终有写作子代理可用。
    """
    rows = (
        (
            await session.execute(
                select(SubagentConfig)
                .where(SubagentConfig.enabled.is_(True))
                .order_by(
                    SubagentConfig.is_builtin.desc(),
                    SubagentConfig.created_at.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return [
            {
                "name": name,
                "label": spec.get("label") or name,
                "description": spec["description"],
                "system_prompt": spec["system_prompt"],
                "mode": spec.get("mode", "isolated"),
                "skills": list(spec.get("skills") or []),
                "tools": list(spec.get("tools") or []),
                "workflows": list(spec.get("workflows") or []),
                "model_ref": None,
                "is_builtin": True,
            }
            for name, spec in BUILTIN_SUBAGENTS.items()
        ]
    return [row_to_spec(r) for r in rows]
