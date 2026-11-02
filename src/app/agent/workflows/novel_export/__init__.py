# -*- coding: utf-8 -*-
"""
模块: app.agent.workflows.novel_export
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 导出类固定流程：

    - `preflight.py`：导出成书预检（E1 结构完整性 → E2 内容完备性 → E3 格式可用性 →
      E4 汇总 checklist；纯规则不调 LLM）。
"""

from app.agent.workflows.novel_export.preflight import run_export_preflight

__all__ = ["run_export_preflight"]
