# -*- coding: utf-8 -*-
"""
模块: app.agent.prompts
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 提示词模板（Jinja2，独立版本化，手册「二、(5) 提示词模块」）。

    模板文件集中在本目录：主代理 / 子代理 / 摘要 / 门禁预检后检（M4）/ 选区改写。
    实现在 `renderer.py`；本文件只做 re-export，与 agent/ 其余子包风格一致。
"""

from __future__ import annotations

from app.agent.prompts.renderer import TEMPLATES_DIR, render

__all__ = ["TEMPLATES_DIR", "render"]
