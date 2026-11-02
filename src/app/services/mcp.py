# -*- coding: utf-8 -*-
"""
模块: app.services.mcp
文件名: mcp.py
作者: xionggaoqing
时间: 2026-10-27
说明: MCP 服务器管理（手册「二、(3) MCP」+「六 → 路由树 · /mcp-servers」）。

    约束：
    - 仅开源免费、无需认证的 MCP（无认证头字段）；
    - stdio 型：命令白名单（npx/uvx/python）+ **二次显式确认**（confirmed_at）+ 命令全文落审计；
    - `/{id}/probe` 探测工具列表；`/{id}/confirm` 二次确认（stdio 专用）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.more.mcp.adapter import (
    STDIO_COMMAND_WHITELIST,
    probe_http,
    probe_stdio,
    validate_stdio_command,
    wrap_tools_async,
)
from app.core.errors import BadRequestError, NotFoundError
from app.core.logging import get_logger
from app.db.base import new_id, utcnow
from app.db.models import McpServer
from app.services import audit

logger = get_logger(__name__)

_VALID_TRANSPORTS = ("stdio", "http", "sse")


def _dto(row: McpServer, *, mask_command: bool = False) -> dict[str, Any]:
    command: Any = None
    if row.command_json:
        try:
            command = json.loads(row.command_json)
        except ValueError:
            command = None
    tools: Any = []
    if row.tools_json:
        try:
            tools = json.loads(row.tools_json)
        except ValueError:
            tools = []
    return {
        "id": row.id,
        "name": row.name,
        "transport": row.transport,
        "command": command,
        "url": row.url,
        "enabled": row.enabled,
        "confirmed_at": row.confirmed_at,
        "needs_confirm": row.transport == "stdio" and row.confirmed_at is None,
        "tools": tools,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def list_servers(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (await session.execute(select(McpServer).order_by(McpServer.created_at.asc()))).scalars().all()
    return [_dto(r) for r in rows]


async def create_server(
    session: AsyncSession,
    *,
    name: str,
    transport: str,
    command: dict[str, Any] | None = None,
    url: str | None = None,
    enabled: bool = False,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise BadRequestError("名称不能为空")
    if transport not in _VALID_TRANSPORTS:
        raise BadRequestError(f"transport 必须是 {_VALID_TRANSPORTS} 之一")

    dup = (await session.execute(select(McpServer).where(McpServer.name == name))).scalar_one_or_none()
    if dup is not None:
        raise BadRequestError(f"MCP 名称已存在：{name}")

    command_json: str | None = None
    detail: dict[str, Any] = {"name": name, "transport": transport}
    if transport == "stdio":
        if not command or not command.get("command"):
            raise BadRequestError("stdio MCP 必须提供 command（JSON：{command, args}）")
        cmd, args = validate_stdio_command(str(command.get("command")), list(command.get("args") or []))
        command_json = json.dumps({"command": cmd, "args": args}, ensure_ascii=False)
        # ★命令全文落审计（安全边界要求）
        detail["command_full"] = f"{cmd} {' '.join(args)}".strip()
        detail["whitelist"] = list(STDIO_COMMAND_WHITELIST)
    else:
        if not (url or "").strip().startswith(("http://", "https://")):
            raise BadRequestError("http/sse MCP 必须提供 http(s) URL")
        url = url.strip()
        detail["url"] = url

    row = McpServer(
        id=new_id(),
        name=name,
        transport=transport,
        command_json=command_json,
        url=url if transport != "stdio" else None,
        enabled=False,  # 新建一律停用，探测+确认后才可启用
        confirmed_at=None,
    )
    session.add(row)
    await session.flush()
    await audit.record(session, "mcp.create", entity_type="mcp_server", entity_id=row.id, detail=detail)
    logger.info("mcp_server_created", name=name, transport=transport)
    return _dto(row)


async def update_server(session: AsyncSession, pk: str, **fields: Any) -> dict[str, Any]:
    row = await session.get(McpServer, pk)
    if row is None:
        raise NotFoundError(f"MCP 不存在：{pk}")

    changes: dict[str, Any] = {}

    if "url" in fields and fields["url"] is not None and row.transport != "stdio":
        url = str(fields["url"]).strip()
        if not url.startswith(("http://", "https://")):
            raise BadRequestError("http/sse MCP 必须提供 http(s) URL")
        if url != row.url:
            changes["url"] = {"from": row.url, "to": url}
            row.url = url
            row.tools_json = None  # URL 变了，工具列表失效

    if "command" in fields and fields["command"] is not None and row.transport == "stdio":
        command = fields["command"]
        cmd, args = validate_stdio_command(str(command.get("command")), list(command.get("args") or []))
        new_json = json.dumps({"command": cmd, "args": args}, ensure_ascii=False)
        if new_json != row.command_json:
            changes["command_full"] = f"{cmd} {' '.join(args)}".strip()
            row.command_json = new_json
            row.confirmed_at = None  # 命令变了必须重新二次确认
            row.tools_json = None

    if "enabled" in fields and fields["enabled"] is not None:
        enabled = bool(fields["enabled"])
        if enabled and row.transport == "stdio" and row.confirmed_at is None:
            raise BadRequestError("stdio MCP 启用前必须完成二次确认（POST /{id}/confirm）")
        if enabled != row.enabled:
            changes["enabled"] = {"from": row.enabled, "to": enabled}
            row.enabled = enabled

    if changes:
        row.updated_at = utcnow()
        await session.flush()
        await audit.record(session, "mcp.update", entity_type="mcp_server", entity_id=pk, detail=changes)

    return _dto(row)


async def delete_server(session: AsyncSession, pk: str) -> dict[str, Any]:
    row = await session.get(McpServer, pk)
    if row is None:
        raise NotFoundError(f"MCP 不存在：{pk}")
    name = row.name
    await session.delete(row)
    await session.flush()
    await audit.record(session, "mcp.delete", entity_type="mcp_server", entity_id=pk, detail={"name": name})
    return {"id": pk, "deleted": True}


async def probe_server(session: AsyncSession, pk: str) -> dict[str, Any]:
    """探测工具列表（不改 enabled；成功则缓存 tools_json）。"""
    row = await session.get(McpServer, pk)
    if row is None:
        raise NotFoundError(f"MCP 不存在：{pk}")

    if row.transport == "stdio":
        command_data = json.loads(row.command_json or "{}")
        tools = await probe_stdio(
            str(command_data.get("command") or ""), list(command_data.get("args") or [])
        )
    else:
        tools = await probe_http(str(row.url or ""), transport=row.transport)

    row.tools_json = json.dumps(tools, ensure_ascii=False, default=str)
    row.updated_at = utcnow()
    await session.flush()
    await audit.record(
        session,
        "mcp.probe",
        entity_type="mcp_server",
        entity_id=pk,
        detail={"name": row.name, "tool_count": len(tools), "tools": [t["name"] for t in tools]},
    )
    return _dto(row)


async def confirm_server(session: AsyncSession, pk: str) -> dict[str, Any]:
    """stdio 型二次显式确认（记录时间 + 命令全文落审计）。"""
    row = await session.get(McpServer, pk)
    if row is None:
        raise NotFoundError(f"MCP 不存在：{pk}")
    if row.transport != "stdio":
        raise BadRequestError("只有 stdio MCP 需要二次确认")

    row.confirmed_at = utcnow()
    row.updated_at = row.confirmed_at
    await session.flush()

    command_data = json.loads(row.command_json or "{}")
    command_full = f"{command_data.get('command', '')} {' '.join(str(a) for a in command_data.get('args') or [])}".strip()
    await audit.record(
        session,
        "mcp.confirm",
        entity_type="mcp_server",
        entity_id=pk,
        detail={"name": row.name, "command_full": command_full, "confirmed_at": row.confirmed_at},
    )
    logger.info("mcp_server_confirmed", name=row.name, command=command_full)
    return _dto(row)


async def enabled_tool_rows(session: AsyncSession) -> list[McpServer]:
    """已启用且（stdio 已确认）的 MCP，供 builder 装配工具。"""
    rows = (
        (
            await session.execute(
                select(McpServer).where(McpServer.enabled.is_(True)).order_by(McpServer.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [r for r in rows if r.transport != "stdio" or r.confirmed_at is not None]


async def wrap_enabled_tools(session: AsyncSession) -> list[Any]:
    """把启用的第三方 MCP 封装为 LangChain tool 列表（单个失败跳过不阻断）。"""

    tools: list[Any] = []
    for row in await enabled_tool_rows(session):
        try:
            wrapped = await wrap_tools_async(row)
            tools.extend(wrapped)
            logger.info("mcp_tools_wrapped", server=row.name, count=len(wrapped))
        except Exception as exc:
            logger.warning("mcp_wrap_failed", server=row.name, error=str(exc))
    return tools
