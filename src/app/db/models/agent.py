# -*- coding: utf-8 -*-
"""
模块: app.db.models.agent
文件名: agent.py
作者: xionggaoqing
时间: 2026-10-27
说明: Agent 域 ORM：agent_sessions / messages / tool_call_logs / agent_runs /
    agent_interrupts（M3）+ subagent_configs / skill_configs（M5）+ mcp_servers（M6）
    （手册「五、数据模型」）。

    消息树用 `parent_id + branch_index`，会话保存 `active_leaf_id`；
    跨库不建外键：LangGraph checkpoint 另存 `data/agent_state.db`。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_id, utcnow


class AgentSession(Base, TimestampMixin):
    """会话：严格按小说项目隔离（`project_id`），一个项目可多会话。"""

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(36), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="新会话")
    #: 消息树活跃叶子（root→active_leaf 为 LLM 上下文路径）
    active_leaf_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: 会话级默认模型（"{provider_id}:{model_id}"）
    model_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    subagent_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_chapter_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: idle|running|awaiting_approval|awaiting_consult|error
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="idle")


class Message(Base):
    """消息树：`parent_id + branch_index`；重新生成 = 同父兄弟分支。"""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session_parent", "session_id", "parent_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    branch_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: user|assistant|tool|system
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    #: normal|steer|notice：普通消息 / 运行中引导消息 / Agent 默认收尾回复
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    content_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: streaming|complete|error|cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="complete")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 输入 token 中命中 provider 前缀缓存的部分（usage_metadata.input_token_details.cache_read）
    cached_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class ToolCallLog(Base):
    """工具调用日志（前端渲染 + 审计 + 导出）。"""

    __tablename__ = "tool_call_logs"
    __table_args__ = (Index("ix_tool_call_logs_session", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    args_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: success|error|rejected
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class AgentRun(Base):
    """一次「用户发消息 → Agent 跑完」的运行（含流程字段）。"""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_session", "session_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: pending|running|awaiting_approval|awaiting_consult|cancelling|cancelled|completed|failed
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    model_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recursion_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: 输入 token 中命中 provider 前缀缓存的累计（缓存命中率 = cached_in / tokens_in）
    cached_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fallback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)
    ended_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: S0|S1|S2|S3|NULL（非生成类运行为空）
    workflow_stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    workflow_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    workflow_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 流程生成目标章（固定流程运行专用）
    target_chapter_id: Mapped[str | None] = mapped_column(String(16), nullable=True)


class AgentInterrupt(Base):
    """中断：approval（工具审批）| consult（流程咨询卡片）。"""

    __tablename__ = "agent_interrupts"
    __table_args__ = (Index("ix_agent_interrupts_run", "run_id", "decided_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: approval|consult
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: approve|edit|reject|respond|option|text（未决时为空）
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
    decided_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class SubagentConfig(Base, TimestampMixin):
    """写作子代理配置（M5）。内置 5 个 + 用户自建。"""

    __tablename__ = "subagent_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: model_ref 覆盖（空则用会话/全局默认）
    model_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tools_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: isolated|fork（fork 下 skills_json 必须为空）
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="isolated")
    permissions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    interrupt_on_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SkillConfig(Base, TimestampMixin):
    """写作技能配置（M5）：`content_md` 物化到 `data/skills/{name}/SKILL.md`。"""

    __tablename__ = "skill_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    #: name 必须等于物化目录名（小写字母数字连字符 ≤64）
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    assets_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class McpServer(Base, TimestampMixin):
    """MCP 服务器配置（M6）：仅开源免费、无需认证。"""

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    #: stdio|http|sse
    transport: Mapped[str] = mapped_column(String(16), nullable=False)
    #: stdio：{"command": "npx", "args": [...]}（白名单可执行文件）
    command_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: stdio 型的用户二次确认时间
    confirmed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tools_json: Mapped[str | None] = mapped_column(Text, nullable=True)
