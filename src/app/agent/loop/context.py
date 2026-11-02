# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.context
文件名: context.py
作者: xionggaoqing
时间: 2026-10-27
说明: 运行期上下文（手册「二、(2)」）。

    `project_id / session_id / run_id / workflow_stage` 经 `context_schema` + `Runtime.context`
    注入中间件与工具，**不使用全局变量/上下文变量传递**（避免并发 run 串数据）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentContext:
    """`create_deep_agent(context_schema=AgentContext)` 的实例类型。"""

    project_id: str
    session_id: str
    run_id: str
    #: S0|S1|S2|S3|""（非生成类运行为空）
    workflow_stage: str = ""
    #: 流程生成目标章（固定流程运行专用）
    target_chapter_id: str = ""
    #: 当前使用的 model_ref（事件里回传，便于前端展示）
    model_ref: str = ""
    #: 由 runner 注入：把自定义事件送进 SSE 环形缓冲
    emit: Any = None
    #: 取消标志容器（协作式取消）：{"cancelled": bool}
    cancel_flag: dict[str, bool] = field(default_factory=dict)
