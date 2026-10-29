# -*- coding: utf-8 -*-
"""
模块: app.services.subagents
文件名: subagents.py
作者: xionggaoqing
时间: 2026-10-27
说明: 子代理配置服务（手册「二、(3) Sub-agents」+「五、数据模型」）。

    内置 5 个写作子代理随首次启动**播种**进 `subagent_configs`（is_builtin=True，
    不可删除，可停用/改提示词/改模型覆盖）；用户可自建子代理。

    装配约束：
    - `name` 小写字母数字连字符、≤64（deepagents SubAgent 命名规则）；
    - `mode=fork` 时 `skills_json` 必须为空（装配时校验，deepagents 实测会抛错）；
    - `model_ref` 覆盖在 builder 里预解析为 ChatModel 实例传入 `SubAgent.model`。
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog import builtin_subagents
from app.core.errors import BadRequestError, NotFoundError
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models import SubagentConfig
from app.services import audit

logger = get_logger(__name__)

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

_VALID_MODES = ("isolated", "fork")


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise BadRequestError(
            f"子代理名不合法：{name!r}（小写字母开头，仅小写字母/数字/连字符，≤64 字符）"
        )
    return name


def validate_mode(mode: str, skills: list[str]) -> str:
    if mode not in _VALID_MODES:
        raise BadRequestError(f"mode 必须是 {_VALID_MODES} 之一，收到：{mode}")
    if mode == "fork" and skills:
        raise BadRequestError("fork 模式继承父上下文，不能配置 skills（deepagents 限制）")
    return mode


def _dto(row: SubagentConfig) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "system_prompt": row.system_prompt,
        "model_ref": row.model_ref,
        "tools": _loads_list(row.tools_json),
        "skills": _loads_list(row.skills_json),
        "mode": row.mode,
        "enabled": row.enabled,
        "is_builtin": row.is_builtin,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _loads_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


async def seed_builtins(session: AsyncSession) -> int:
    """播种内置子代理（幂等：已存在的按 name 跳过，不覆盖用户改动）。"""
    existing = set((await session.execute(select(SubagentConfig.name))).scalars().all())
    created = 0
    for name, spec in builtin_subagents().items():
        if name in existing:
            continue
        session.add(
            SubagentConfig(
                id=new_id(),
                name=name,
                description=spec["description"],
                system_prompt=spec["system_prompt"],
                mode=spec.get("mode", "isolated"),
                skills_json=json.dumps(spec.get("skills") or [], ensure_ascii=False),
                enabled=True,
                is_builtin=True,
            )
        )
        created += 1
    if created:
        await session.flush()
        logger.info("builtin_subagents_seeded", count=created)
    return created


async def list_subagents(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(SubagentConfig).order_by(
                    SubagentConfig.is_builtin.desc(), SubagentConfig.created_at.asc()
                )
            )
        )
        .scalars()
        .all()
    )
    return [_dto(r) for r in rows]


async def create_subagent(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    system_prompt: str | None = None,
    model_ref: str | None = None,
    tools: list[str] | None = None,
    skills: list[str] | None = None,
    mode: str = "isolated",
    enabled: bool = True,
) -> dict[str, Any]:
    name = validate_name(name)
    validate_mode(mode, skills or [])
    if not (description or "").strip():
        raise BadRequestError("description 不能为空（主代理据此决定何时委派）")

    dup = (await session.execute(select(SubagentConfig).where(SubagentConfig.name == name))).scalar_one_or_none()
    if dup is not None:
        raise BadRequestError(f"子代理名已存在：{name}")

    row = SubagentConfig(
        id=new_id(),
        name=name,
        description=description.strip(),
        system_prompt=system_prompt,
        model_ref=model_ref or None,
        tools_json=json.dumps(tools, ensure_ascii=False) if tools else None,
        skills_json=json.dumps(skills, ensure_ascii=False) if skills else None,
        mode=mode,
        enabled=enabled,
        is_builtin=False,
    )
    session.add(row)
    await session.flush()
    await audit.record(
        session, "subagent.create", entity_type="subagent", entity_id=row.id, detail={"name": name, "mode": mode}
    )
    return _dto(row)


async def update_subagent(session: AsyncSession, pk: str, **fields: Any) -> dict[str, Any]:
    row = await session.get(SubagentConfig, pk)
    if row is None:
        raise NotFoundError(f"子代理不存在：{pk}")

    changes: dict[str, Any] = {}

    name = fields.get("name")
    if name is not None and name != row.name:
        if row.is_builtin:
            raise BadRequestError("内置子代理不可改名")
        name = validate_name(name)
        dup = (
            await session.execute(
                select(SubagentConfig).where(SubagentConfig.name == name, SubagentConfig.id != pk)
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise BadRequestError(f"子代理名已存在：{name}")
        changes["name"] = {"from": row.name, "to": name}
        row.name = name

    for simple in ("description", "system_prompt", "model_ref", "mode", "enabled"):
        value = fields.get(simple)
        if value is None:
            continue
        if simple == "enabled":
            value = bool(value)
        if getattr(row, simple) != value:
            changes[simple] = {"from": getattr(row, simple), "to": value}
            setattr(row, simple, value)

    tools = fields.get("tools")
    if tools is not None:
        new_json = json.dumps(tools, ensure_ascii=False) if tools else None
        if new_json != row.tools_json:
            changes["tools"] = tools
            row.tools_json = new_json

    skills = fields.get("skills")
    if skills is not None:
        new_json = json.dumps(skills, ensure_ascii=False) if skills else None
        if new_json != row.skills_json:
            changes["skills"] = skills
            row.skills_json = new_json

    # fork + skills 组合校验（mode 或 skills 任一变更后都要检查）
    validate_mode(row.mode, _loads_list(row.skills_json))

    if changes:
        row.updated_at = utcnow()
        await session.flush()
        await audit.record(session, "subagent.update", entity_type="subagent", entity_id=pk, detail=changes)

    return _dto(row)


async def delete_subagent(session: AsyncSession, pk: str) -> dict[str, Any]:
    row = await session.get(SubagentConfig, pk)
    if row is None:
        raise NotFoundError(f"子代理不存在：{pk}")
    if row.is_builtin:
        raise BadRequestError("内置子代理不可删除（可停用）")
    name = row.name
    await session.delete(row)
    await session.flush()
    await audit.record(session, "subagent.delete", entity_type="subagent", entity_id=pk, detail={"name": name})
    return {"id": pk, "deleted": True}


async def reset_builtin(session: AsyncSession, pk: str) -> dict[str, Any]:
    """把内置子代理恢复为出厂配置（提示词/描述/mode/skills）。"""
    row = await session.get(SubagentConfig, pk)
    if row is None:
        raise NotFoundError(f"子代理不存在：{pk}")
    catalog = builtin_subagents()
    if not row.is_builtin or row.name not in catalog:
        raise BadRequestError("只有内置子代理支持恢复出厂配置")
    spec = catalog[row.name]
    row.description = spec["description"]
    row.system_prompt = spec["system_prompt"]
    row.mode = spec.get("mode", "isolated")
    row.skills_json = json.dumps(spec.get("skills") or [], ensure_ascii=False)
    #: v1028：恢复默认时一并清除自定义工具勾选
    row.tools_json = None
    row.model_ref = None
    row.enabled = True
    row.updated_at = utcnow()
    await session.flush()
    await audit.record(session, "subagent.reset", entity_type="subagent", entity_id=pk, detail={"name": row.name})
    return _dto(row)
