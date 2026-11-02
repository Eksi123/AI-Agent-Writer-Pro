# -*- coding: utf-8 -*-
"""
模块: app.agent.workflows.writing_steps
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 四阶段固定流程编排（手册「二、(6)」）。S0/S1/S3 直连 LLM，S2 进 deep agent。
"""

from app.agent.workflows.writing_steps.orchestrator import WorkflowRunner, advance_workflow
from app.agent.workflows.writing_steps.state import (
    POSTCHECK_MAX_RETRY,
    ConsultRequired,
    WorkflowState,
    WorkflowTarget,
    llm_json,
)

__all__ = [
    "WorkflowRunner",
    "advance_workflow",
    "WorkflowState",
    "WorkflowTarget",
    "ConsultRequired",
    "POSTCHECK_MAX_RETRY",
    "llm_json",
]
