#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.mcp
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 内置天气 MCP（Open-Meteo）+ 第三方 MCP 工具封装（手册「二、(3) MCP」）。

    - `server.py`：内置天气 MCP（mcp 2.x `MCPServer`），提供 get_current_weather / get_forecast；
    - `adapter.py`：第三方 MCP → LangChain tool 自封装（`langchain-mcp-adapters` 与 mcp 2.3.0
      不兼容——实测 ImportError RequestContext，按手册回落用 mcp 官方 client 自封装）；
    - 进程内直连：内置 MCP 的工具经 `call_tool` 直接调用，同时可 `run_stdio_async` 供外部接入。
"""

from app.agent.mcp.server import build_weather_server, weather_tools

__all__ = ["build_weather_server", "weather_tools"]
