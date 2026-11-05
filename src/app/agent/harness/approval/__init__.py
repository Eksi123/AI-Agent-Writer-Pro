# -*- coding: utf-8 -*-
"""
模块: app.agent.harness.approval
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 高危写入 HITL 审批：`WriteApprovalMiddleware` 按**参数**判定风险
    （整章覆盖 / 大范围 edit / 项目内 delete）→ `interrupt()` 转人工审批。

    与 `interrupt_on={"delete": True}`（工具级开关，builder 配置）互补。
    装配位置必须为中间件栈**最外层**：GraphInterrupt 若被外层中间件吞掉，
    审批将永远无法暂停 run（详见 loop/builder.build_middleware_stack 注释）。
"""

from app.agent.harness.approval.write_approval import (
    INTERRUPT_KIND,
    WriteApprovalMiddleware,
    classify_risk,
)

__all__ = ["INTERRUPT_KIND", "WriteApprovalMiddleware", "classify_risk"]
