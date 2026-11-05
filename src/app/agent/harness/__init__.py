# -*- coding: utf-8 -*-
"""
模块: app.agent.harness
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: Agent 运行安全 Harness。

    收纳所有**安全类**措施（Agent 运行必备流程与 workflows 写作流程不在此列）：
    - `sandbox/`：CompositeBackend 虚拟路径路由 + 8 条有序 permissions + 虚拟路径解析；
    - `llm/`：token 预算（计数/阈值报告），重试/降级/限次的装配在 loop/builder；
    - `approval/`：WriteApprovalMiddleware（参数级高危写入 → HITL 中断审批）；
    - `guard/`：WorkspaceGuardMiddleware（路径校验第二道 + 审计 + 错误指纹阻断）；
    - `lock/`：ContentLockMiddleware（AI 编辑锁，防用户与 Agent 并发写同一章）。
"""
