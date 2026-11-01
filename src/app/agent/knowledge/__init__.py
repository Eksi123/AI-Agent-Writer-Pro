# -*- coding: utf-8 -*-
"""
模块: app.agent.knowledge
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 知识库服务（手册「二、(3) 知识库服务函数」，非 Agent 工具，服务层内部调用）。

    - `build_recap`：前情提要，经 `RecapInjectionMiddleware.abefore_model` 注入系统提示；
    - `summarize_chapter`：章节摘要生成（定稿或 Agent 运行结束后触发），写 `summaries/`；
    - 大纲章简介解析：供门禁 G0/G1/G3（M4）。

    实现分布：`chapters.py`（章节取数）+ `recap.py`（前情提要）+ `summarize.py`（摘要生成）。
    本文件只做 re-export，与 agent/ 其余子包风格一致。
"""

from __future__ import annotations

from app.agent.knowledge.chapters import list_chapters_ordered
from app.agent.knowledge.recap import build_pinned_chapters, build_recap
from app.agent.knowledge.summarize import summarize_all_chapters, summarize_chapter

__all__ = [
    "build_pinned_chapters",
    "build_recap",
    "list_chapters_ordered",
    "summarize_chapter",
    "summarize_all_chapters",
]
