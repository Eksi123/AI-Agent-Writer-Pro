#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.mcp.adapter
文件名: adapter.py
作者: xionggaoqing
时间: 2026-10-27
说明: 第三方 MCP → LangChain tool 自封装（手册「二、(3) MCP 接入方式」回落路径）。

    实测：`langchain-mcp-adapters` 与 `mcp 2.3.0` 不兼容
    （`ImportError: cannot import name 'RequestContext' from 'mcp.shared.context'`），
    故按手册回落后用 `mcp` 官方 client（stdio_client / ClientSession / streamablehttp_client）自封装。

    安全边界（手册「⚠️ stdio MCP 的安全边界」）：
    - stdio 型等价受限任意命令执行：可执行文件**白名单**（默认 npx/uvx/python），拒绝任意路径命令；
    - 用户添加时须**二次显式确认**（`mcp_servers.confirmed_at`）并展示完整命令；
    - 命令全文落 `audit_logs`；
    - 仅接开源免费、无认证 MCP（无认证头/Token 配置字段）。
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

from langchain_core.tools import StructuredTool

from app.core.errors import BadRequestError, DependencyUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: stdio 可执行文件白名单（命令名精确匹配，拒绝路径形式）
STDIO_COMMAND_WHITELIST = ("npx", "uvx", "python")

_PROBE_TIMEOUT = 15.0


def validate_stdio_command(command: str, args: list[str]) -> tuple[str, list[str]]:
    """白名单校验：命令必须是白名单内的裸命令名（不含路径分隔符）。

    Returns:
        (command, args) 规范化结果

    Raises:
        BadRequestError: 命令不在白名单 / 含路径。
    """
    cmd = (command or "").strip()
    args = [str(a) for a in (args or [])]
    if not cmd:
        raise BadRequestError("stdio MCP 必须提供 command")
    if "/" in cmd or "\\" in cmd or ":" in cmd:
        raise BadRequestError(f"拒绝路径形式的命令：{cmd}（仅允许白名单命令名 {STDIO_COMMAND_WHITELIST}）")
    if cmd not in STDIO_COMMAND_WHITELIST:
        raise BadRequestError(f"命令 {cmd} 不在白名单内（仅允许 {', '.join(STDIO_COMMAND_WHITELIST)}）")
    return cmd, args


async def probe_stdio(command: str, args: list[str], *, env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """探测 stdio MCP 的工具列表（拉起子进程 → initialize → list_tools → 关闭）。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    cmd, cmd_args = validate_stdio_command(command, args)
    params = StdioServerParameters(command=cmd, args=cmd_args, env=env or None)
    try:
        async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            return [_tool_info(t) for t in tools.tools]
    except BadRequestError:
        raise
    except Exception as exc:
        logger.warning("mcp_stdio_probe_failed", command=cmd, error=str(exc))
        raise DependencyUnavailableError(f"stdio MCP 探测失败：{type(exc).__name__}: {exc}") from exc


async def probe_http(url: str, *, transport: str = "streamable_http") -> list[dict[str, Any]]:
    """探测 streamable-http / SSE MCP 的工具列表。"""
    if not (url or "").strip().startswith(("http://", "https://")):
        raise BadRequestError(f"非法 URL：{url}")
    try:
        async with _http_session(url, transport) as client:
            await client.initialize()
            tools = await client.list_tools()
            return [_tool_info(t) for t in tools.tools]
    except BadRequestError:
        raise
    except Exception as exc:
        logger.warning("mcp_http_probe_failed", url=url, error=str(exc))
        raise DependencyUnavailableError(f"MCP 探测失败：{type(exc).__name__}: {exc}") from exc


def _tool_info(t: Any) -> dict[str, Any]:
    return {
        "name": t.name,
        "description": (t.description or "")[:500],
        "input_schema": t.inputSchema if hasattr(t, "inputSchema") else {},
    }


class _http_session:
    """按 transport 打开 ClientSession 的异步上下文（http/sse）。"""

    def __init__(self, url: str, transport: str) -> None:
        self.url = url
        self.transport = transport

    async def __aenter__(self) -> Any:
        from mcp import ClientSession

        self._stack = AsyncExitStack()
        try:
            if self.transport == "sse":
                from mcp.client.sse import sse_client

                read, write = await self._stack.enter_async_context(sse_client(self.url))
            else:
                from mcp.client.streamable_http import streamablehttp_client

                read, write, _get_session = await self._stack.enter_async_context(
                    streamablehttp_client(self.url)
                )
            session = await self._stack.enter_async_context(ClientSession(read, write))
        except Exception:
            await self._stack.aclose()
            raise
        return session

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.aclose()


# ---------------------------------------------------------------------------
# LangChain tool 封装（每次调用建立短连接；单用户本地场景足够）
# ---------------------------------------------------------------------------


async def wrap_tools_async(row: Any) -> list[StructuredTool]:
    """把 `mcp_servers` 行封装为 LangChain tool 列表。

    Args:
        row: McpServer ORM 行（transport/command_json/url）。
    """
    if row.transport == "stdio":
        try:
            command_data = json.loads(row.command_json or "{}")
        except ValueError:
            command_data = {}
        command = str(command_data.get("command") or "")
        args = [str(a) for a in (command_data.get("args") or [])]
        validate_stdio_command(command, args)
        infos = await probe_stdio(command, args)
        invoker = lambda name, arguments: _call_stdio(command, args, name, arguments)  # noqa: E731
    else:
        url = str(row.url or "")
        infos = await probe_http(url, transport=row.transport)
        invoker = lambda name, arguments: _call_http(url, row.transport, name, arguments)  # noqa: E731

    tools: list[StructuredTool] = []
    for info in infos:
        # 工具名只允许小写字母数字下划线（LangChain 约束），非法字符转下划线
        safe_server = _safe(row.name)
        safe_tool = _safe(info["name"])
        name = f"mcp_{safe_server}_{safe_tool}"[:64]
        tool_obj = StructuredTool.from_function(
            coroutine=_make_coro(invoker, info["name"]),
            name=name,
            description=f"[MCP:{row.name}] {info['description']}"[:1024],
        )
        tools.append(tool_obj)
    return tools


def _safe(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9_]", "_", (text or "").lower()).strip("_") or "x"


def _make_coro(invoker: Any, tool_name: str) -> Any:
    async def _call(**kwargs: Any) -> str:
        try:
            return await invoker(tool_name, kwargs)
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

    return _call


async def _call_stdio(command: str, args: list[str], tool_name: str, arguments: dict[str, Any]) -> str:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        result = await client.call_tool(tool_name, arguments)
        return _result_text(result)


async def _call_http(url: str, transport: str, tool_name: str, arguments: dict[str, Any]) -> str:
    async with _http_session(url, transport) as client:
        await client.initialize()
        result = await client.call_tool(tool_name, arguments)
        return _result_text(result)


def _result_text(result: Any) -> str:
    """CallToolResult → 文本（多块拼接；isError 时前缀标注）。"""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        else:
            parts.append(json.dumps(getattr(block, "model_dump", lambda: {})(), ensure_ascii=False, default=str))
    text = "\n".join(parts) or json.dumps({"structured": getattr(result, "structuredContent", None)}, ensure_ascii=False, default=str)
    if getattr(result, "isError", False):
        return f"[MCP 工具返回错误] {text}"
    return text
