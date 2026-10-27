# -*- coding: utf-8 -*-
"""
模块: app.agent.prompts.renderer
文件名: renderer.py
作者: xionggaoqing
时间: 2026-10-27
说明: Jinja2 环境与模板渲染（手册「二、(5) 提示词模块」，独立版本化）。

    模板文件集中在 `agent/prompts/`：主代理 / 子代理 / 摘要 / 门禁预检后检（M4）/ 选区改写。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR), encoding="utf-8"),
        autoescape=select_autoescape(disabled_extensions=("md.j2", "j2", "md", "txt"), default=False),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render(template_name: str, **context: Any) -> str:
    """渲染模板（`StrictUndefined`：漏传变量立即报错，便于回归）。"""
    return _env().get_template(template_name).render(**context)
