# -*- coding: utf-8 -*-
"""
模块: app.agent.workflows.book_consistency
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 全书审查类固定流程：

    - `book_consistency.py`：全书一致性审查（A1 素材收集按卷分批 → A2 每卷一次
      LLM 结构化审查 → A3 汇总落报告 `outline/consistency-audit.md`）。
"""

from app.agent.workflows.book_consistency.audit import run_book_consistency_audit

__all__ = ["run_book_consistency_audit"]
