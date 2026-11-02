# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.prompts.renderer
文件名: renderer.py
作者: xionggaoqing
时间: 2026-10-27
说明: Jinja2 环境与模板渲染（手册「二、(5) 提示词模块」，独立版本化）。

    ★v1102（架构优化）：模板按归属分散在多处，渲染器用**多目录 FileSystemLoader**
    统一查找（调用方仍只传文件名，无需知道模板在哪）：
    - `loop/prompts/`：主代理系统提示（`main_agent.md.j2`）、章节摘要（`summarize_chapter.md.j2`）；
    - `workflows/writing_steps/prompts/`：流程预检 / 生成 / 后检 / 大纲代写模板；
    - `workflows/book_consistency/prompts/`：全书一致性审查模板。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

#: 主模板目录（主代理系统提示）
TEMPLATES_DIR = Path(__file__).resolve().parent

#: 全部模板搜索路径（顺序即查找优先级）：本目录 + 各流程的 prompts 目录
_TEMPLATE_PATHS: tuple[Path, ...] = (
    TEMPLATES_DIR,
    TEMPLATES_DIR.parents[1] / "workflows" / "writing_steps" / "prompts",
    TEMPLATES_DIR.parents[1] / "workflows" / "book_consistency" / "prompts",
)


@lru_cache(maxsize=1)
def _env() -> Environment:
    dirs = [str(p) for p in _TEMPLATE_PATHS if p.is_dir()]
    return Environment(
        loader=FileSystemLoader(dirs, encoding="utf-8"),
        autoescape=select_autoescape(disabled_extensions=("md.j2", "j2", "md", "txt"), default=False),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render(template_name: str, **context: Any) -> str:
    """渲染模板（`StrictUndefined`：漏传变量立即报错，便于回归）。"""
    return _env().get_template(template_name).render(**context)
