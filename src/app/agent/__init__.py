# -*- coding: utf-8 -*-
"""
模块: app.agent
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: AI Agent 全部代码逻辑（手册「三、(1) 目录边界」，架构优化 ）。

    子包（顶层只有 7 个目录）：
    - `loop/`：Agent 运行核心——装配（builder）、运行（runner）、事件流（streaming）、
      上下文处理（knowledge/）、LLM 调用（llm/）、系统提示词（prompts/）、
      功能性中间件（middleware/）、checkpoint 持久化；
    - `subagents/`：子代理——builtin/（4 个内置：planner/writer/reviewer/web-searcher，
      各含 spec.py + prompt.md）+ user/（用户自建装载）+ builder.py（装配）；
    - `tools/`：工具与技能合并——builtin/（**只放内置工具实现**：web_search）
      + registry.py（自定义工具装配清单，非工具本体）
      + users/（用户自建技能装载；技能 = 用户自建，无随包出厂技能目录）；
    - `workflows/`：写作固定流程，按分类分目录——chapter_workflow/（章节生成四阶段
      固定流程，服务层硬编排）+ audit/（全书一致性审查）+ export/（导出成书预检）；
    - `harness/`：安全措施——sandbox/（虚拟路径+权限）、llm/（预算+容错装配）、
      approval/（高危写入审批）、guard/（路径守卫+错误阻断）、lock/（AI 编辑锁）；
    - `tracing/`：运行消息与日志的记录/归类/处理/传递——sse/（事件总线）
      + messages/（消息树与增量同步）；
    - `more/`：其他组件——mcp/（天气 MCP + 第三方适配）、langfuse/（观测）。

    Agent 逻辑不外溢到 api/services；非 Agent 业务代码不进入本包。
    应用设置类（技能/子代理的 DB 配置 CRUD）在 services/，本包只保留运行期装载。

    目录重组后旧 import 路径（app.agent.memory 等）已全部
    迁移为新路径（含外部 api/services/core/tests），无兼容别名层。
"""
