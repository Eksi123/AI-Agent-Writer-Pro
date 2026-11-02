# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.prompts
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 提示词模板（Jinja2，独立版本化，手册「二、(5) 提示词模块」）。

    ★v1102（架构优化）：主代理模板留在本目录（`main_agent.md.j2`）；
    流程预检/生成/后检模板随流程迁至 `agent/workflows/writing_steps/prompts/`。
    实现在 `renderer.py`；本文件只做 re-export。
"""

from __future__ import annotations

from app.agent.loop.prompts.renderer import TEMPLATES_DIR, render

__all__ = ["TEMPLATES_DIR", "render"]
