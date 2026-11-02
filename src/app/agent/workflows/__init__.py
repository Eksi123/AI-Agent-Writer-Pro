# -*- coding: utf-8 -*-
"""
模块: app.agent.workflows
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: 写作固定流程（架构优化 v1102，手册「二、(6)」）。

    收纳写作过程中**服务层硬编排**的固定流程（非 Agent 自觉执行）：
    - `writing_steps/`：章节生成四阶段固定流程（S0 大纲存在性 → S1 衔接预检 → S2 生成 → S3 匹配后检），
      含意图识别（intent）、状态机（state）、编排（orchestrator）、运行（runner）
      与流程专用提示词模板（prompts/）；
    - `book_consistency/audit.py`：全书一致性审查（A1 素材收集按卷分批 →
      A2 每卷一次 LLM 结构化审查 → A3 汇总落报告 `outline/consistency-audit.md`）；
    - `novel_export/preflight.py`：导出成书预检（E1 结构完整性 → E2 内容完备性 →
      E3 格式可用性 → E4 汇总 checklist；纯规则不调 LLM）。

    子代理与流程的绑定关系声明在 `subagents/builtin/{name}/spec.py` 的 `workflows` 字段
    （planner/writer/reviewer → ["writing_steps"]；reviewer 的审查维度与
    book_consistency 同域），经 `subagents.builder.workflows_of()` 查询。
"""

#: 流程注册表（名称 → 运行入口的模块路径；诊断与文档用）
WORKFLOWS: dict[str, str] = {
    "writing_steps": "app.agent.workflows.writing_steps",
    "book_consistency": "app.agent.workflows.book_consistency.audit",
    "novel_export": "app.agent.workflows.novel_export.preflight",
}
