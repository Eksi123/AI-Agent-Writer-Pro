# -*- coding: utf-8 -*-
"""
模块: app.agent.gates
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: 四阶段门禁编排（手册「二、(6)」）。G0/G1/G3 直连 LLM，G2 进 deep agent。
"""

from app.agent.gates.orchestrator import GateRunner, advance_gate
from app.agent.gates.state import (
    G3_MAX_RETRY,
    ConsultRequired,
    GateState,
    GateTarget,
    llm_json,
)

__all__ = [
    "GateRunner",
    "advance_gate",
    "GateState",
    "GateTarget",
    "ConsultRequired",
    "G3_MAX_RETRY",
    "llm_json",
]
