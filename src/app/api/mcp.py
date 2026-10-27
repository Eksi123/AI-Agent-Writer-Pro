#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.api.mcp
文件名: mcp.py
作者: xionggaoqing
时间: 2026-10-27
说明: MCP 服务管理 API（手册「六 → 路由树 · 配置」`/mcp-servers` CRUD + probe + confirm）。

    安全边界（手册「二、(3) ⚠️ stdio MCP」）：仅接开源免费、无需认证的 MCP；
    stdio 型可执行文件走白名单（npx/uvx/python）、须二次显式确认、命令全文落审计。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.services import mcp as mcp_service

router = APIRouter(tags=["mcp"])


class McpCreate(BaseModel):
    name: str = Field(max_length=100)
    transport: str = Field(pattern="^(stdio|http|sse)$")
    #: stdio：{"command": "npx", "args": [...]}（白名单命令）
    command: dict[str, Any] | None = None
    url: str | None = Field(default=None, max_length=500)


class McpUpdate(BaseModel):
    command: dict[str, Any] | None = None
    url: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


@router.get("/mcp-servers")
async def list_servers(session: AsyncSession = Depends(get_session)) -> dict:
    return ok(await mcp_service.list_servers(session))


@router.post("/mcp-servers")
async def create_server(body: McpCreate, session: AsyncSession = Depends(get_session)) -> dict:
    """新建 MCP（一律停用；stdio 型须探测 + 二次确认后才可启用）。"""
    dto = await mcp_service.create_server(
        session, name=body.name, transport=body.transport, command=body.command, url=body.url
    )
    await session.commit()
    return ok(dto)


@router.patch("/mcp-servers/{pk}")
async def update_server(pk: str, body: McpUpdate, session: AsyncSession = Depends(get_session)) -> dict:
    """改 stdio 命令会清空 confirmed_at（须重新二次确认）。"""
    dto = await mcp_service.update_server(session, pk, **body.model_dump(exclude_unset=True))
    await session.commit()
    return ok(dto)


@router.delete("/mcp-servers/{pk}")
async def delete_server(pk: str, session: AsyncSession = Depends(get_session)) -> dict:
    dto = await mcp_service.delete_server(session, pk)
    await session.commit()
    return ok(dto)


@router.post("/mcp-servers/{pk}/probe")
async def probe_server(pk: str, session: AsyncSession = Depends(get_session)) -> dict:
    """探测工具列表（stdio 型会拉起子进程）。失败返回 503/50301。"""
    dto = await mcp_service.probe_server(session, pk)
    await session.commit()
    return ok(dto)


@router.post("/mcp-servers/{pk}/confirm")
async def confirm_server(pk: str, session: AsyncSession = Depends(get_session)) -> dict:
    """stdio 型二次显式确认（前端须先展示完整命令；命令全文落审计）。"""
    dto = await mcp_service.confirm_server(session, pk)
    await session.commit()
    return ok(dto)
