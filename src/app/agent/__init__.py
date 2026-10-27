#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: AI Agent 全部代码逻辑（手册「三、(1) 目录边界」）。

    子包：llm（接入/参数/预算）、loop（装配/运行/事件/中间件）、gates（门禁 G0-G3）、
    tools（web_search）、subagents、skills、mcp、memory（消息树）、knowledge（摘要/前情提要）、
    sandbox（backend/permissions）、prompts、tracing（Langfuse v5）。

    Agent 逻辑不外溢到 api/services；非 Agent 业务代码不进入本包。
"""

