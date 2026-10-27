#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.mcp.server
文件名: server.py
作者: xionggaoqing
时间: 2026-10-27
说明: 内置天气 MCP 服务器（Open-Meteo，手册「二、(3) MCP」）。

    实现基类 `mcp.server.mcpserver.MCPServer`（mcp 2.3.0，`mcp.server.fastmcp` 已移除）。
    天气逻辑全部在 `services/weather.py`（组件 API 与本 MCP 共用，避免两份实现漂移）。

    使用方式：
    - 进程内：`weather_tools()` 返回 LangChain tool 列表，并入 `create_deep_agent(tools=...)`；
    - 子进程/外部：`python -m app.agent.mcp.server` 以 stdio 运行标准 MCP 服务。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool
from mcp.server.mcpserver import MCPServer

from app.core.logging import get_logger

logger = get_logger(__name__)

SERVER_NAME = "open-meteo-weather"


def build_weather_server() -> MCPServer:
    """构建内置天气 MCP 服务器。"""
    server = MCPServer(
        name=SERVER_NAME,
        instructions="免费开源天气服务（Open-Meteo）：当前天气与多日预报，支持中文城市名。",
    )

    @server.tool(name="get_current_weather", description="查询指定城市的当前天气（温度/体感/湿度/风速/天气状况）。")
    async def get_current_weather(city: str) -> str:
        """city: 城市名（中文或英文，如「杭州」「Beijing」）。"""
        from app.services import weather

        try:
            data = await weather.current_weather(city)
            return json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

    @server.tool(name="get_forecast", description="查询指定城市未来 1-7 天的天气预报（每日最高/最低温、降水概率）。")
    async def get_forecast(city: str, days: int = 3) -> str:
        """city: 城市名；days: 预报天数（1-7）。"""
        from app.services import weather

        try:
            data = await weather.forecast(city, days=days)
            return json.dumps(data, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

    return server


# ---------------------------------------------------------------------------
# LangChain tool 封装（进程内直连 MCP server.call_tool，经 MCP 协议保证形状一致）
# ---------------------------------------------------------------------------


@tool("get_current_weather", parse_docstring=True)
async def get_current_weather_tool(city: str) -> str:
    """查询指定城市的当前天气（温度/体感/湿度/风速/天气状况）。

    Args:
        city: 城市名（中文或英文，如「杭州」「Beijing」）。
    """
    from app.services import weather

    try:
        data = await weather.current_weather(city)
        return json.dumps(data, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


@tool("get_forecast", parse_docstring=True)
async def get_forecast_tool(city: str, days: int = 3) -> str:
    """查询指定城市未来 1-7 天的天气预报。

    Args:
        city: 城市名（中文或英文）。
        days: 预报天数（1-7，默认 3）。
    """
    from app.services import weather

    try:
        data = await weather.forecast(city, days=days)
        return json.dumps(data, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def weather_tools() -> list[Any]:
    """内置天气工具（LangChain tool 形式，进程内直连 services.weather）。"""
    return [get_current_weather_tool, get_forecast_tool]


async def main_stdio() -> None:
    """以 stdio 运行 MCP 服务（供外部 MCP 客户端接入）。"""
    server = build_weather_server()
    await server.run_stdio_async()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main_stdio())
