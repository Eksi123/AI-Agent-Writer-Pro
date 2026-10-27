#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.api.subagents
文件名: subagents.py
作者: xionggaoqing
时间: 2026-10-27
说明: 写作子代理管理 API（手册「六 → 路由树 · 配置」`/subagents` CRUD）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.services import subagents as subagents_service

router = APIRouter(tags=["subagents"])


class SubagentCreate(BaseModel):
    name: str = Field(max_length=64)
    description: str = Field(max_length=500)
    system_prompt: str | None = None
    model_ref: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    mode: str = "isolated"
    enabled: bool = True


class SubagentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    system_prompt: str | None = None
    model_ref: str | None = None
    tools: list[str] | None = None
    skills: list[str] | None = None
    mode: str | None = None
    enabled: bool | None = None


@router.get("/subagents")
async def list_subagents(session: AsyncSession = Depends(get_session)) -> dict:
    """内置（已播种）+ 用户自建子代理。"""
    return ok(await subagents_service.list_subagents(session))


@router.post("/subagents")
async def create_subagent(body: SubagentCreate, session: AsyncSession = Depends(get_session)) -> dict:
    dto = await subagents_service.create_subagent(
        session,
        name=body.name,
        description=body.description,
        system_prompt=body.system_prompt,
        model_ref=body.model_ref,
        tools=body.tools,
        skills=body.skills,
        mode=body.mode,
        enabled=body.enabled,
    )
    await session.commit()
    return ok(dto)


@router.patch("/subagents/{pk}")
async def update_subagent(pk: str, body: SubagentUpdate, session: AsyncSession = Depends(get_session)) -> dict:
    dto = await subagents_service.update_subagent(session, pk, **body.model_dump(exclude_unset=True))
    await session.commit()
    return ok(dto)


@router.delete("/subagents/{pk}")
async def delete_subagent(pk: str, session: AsyncSession = Depends(get_session)) -> dict:
    """内置子代理不可删除（可停用）。"""
    dto = await subagents_service.delete_subagent(session, pk)
    await session.commit()
    return ok(dto)


@router.post("/subagents/{pk}/reset")
async def reset_subagent(pk: str, session: AsyncSession = Depends(get_session)) -> dict:
    """恢复内置子代理出厂配置（提示词/描述/mode/skills）。"""
    dto = await subagents_service.reset_builtin(session, pk)
    await session.commit()
    return ok(dto)
