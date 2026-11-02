# -*- coding: utf-8 -*-
"""
模块: app.api.skills
文件名: skills.py
作者: xionggaoqing
时间: 2026-10-27
说明: 写作技能管理 API（手册「六 → 路由树 · 配置」`/skills` CRUD + 物化）。

    技能存 `skill_configs` 并物化到 `data/skills/{name}/SKILL.md`，经 `/skills/user/`
    路由供 Agent 只读加载；内置技能随包只读，不在 DB 中。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.services import skills as skills_service

router = APIRouter(tags=["skills"])


class SkillCreate(BaseModel):
    name: str = Field(max_length=64)
    description: str = Field(max_length=1024)
    body: str = ""
    allowed_tools: list[str] | None = None
    assets: list[dict[str, Any]] | None = None
    enabled: bool = True


class SkillUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1024)
    body: str | None = None
    allowed_tools: list[str] | None = None
    assets: list[dict[str, Any]] | None = None
    enabled: bool | None = None


@router.get("/skills")
async def list_skills(session: AsyncSession = Depends(get_session)) -> dict:
    """用户自建技能（可 CRUD/启停）+ 内置基础工具（只读呈现，启用置灰）。"""
    return ok(await skills_service.list_skills(session))


@router.post("/skills")
async def create_skill(body: SkillCreate, session: AsyncSession = Depends(get_session)) -> dict:
    """新建技能：入库 + 物化到 data/skills/{name}/SKILL.md。"""
    dto = await skills_service.create_skill(
        session,
        name=body.name,
        description=body.description,
        body=body.body,
        allowed_tools=body.allowed_tools,
        assets=body.assets,
        enabled=body.enabled,
    )
    await session.commit()
    return ok(dto)


@router.patch("/skills/{pk}")
async def update_skill(pk: str, body: SkillUpdate, session: AsyncSession = Depends(get_session)) -> dict:
    """改名会迁移物化目录；frontmatter `name` 始终与目录名一致。"""
    dto = await skills_service.update_skill(session, pk, **body.model_dump(exclude_unset=True))
    await session.commit()
    return ok(dto)


@router.delete("/skills/{pk}")
async def delete_skill(pk: str, session: AsyncSession = Depends(get_session)) -> dict:
    """删除技能并移除物化目录（内置技能不可删）。"""
    dto = await skills_service.delete_skill(session, pk)
    await session.commit()
    return ok(dto)


@router.post("/skills/resync")
async def resync_skills(session: AsyncSession = Depends(get_session)) -> dict:
    """按 DB 状态重新物化全部用户技能（修复目录漂移）。"""
    dto = await skills_service.resync_materialization(session)
    await session.commit()
    return ok(dto)
