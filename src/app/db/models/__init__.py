#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.db.models
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: ORM 模型（手册「五、数据模型」）。

    M1 落地：novels / chapter_revisions / reading_progress / user_settings / project_index / audit_logs。
    M3 落地：agent_sessions / messages / tool_call_logs / agent_runs / agent_interrupts。
    M5 落地：subagent_configs / skill_configs。
    M6 落地：mcp_servers。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_id, utcnow


class Novel(Base, TimestampMixin):
    """项目登记表：只做登记 + 外键锚点，内容以文件为事实源，可从工作区全量重建。"""

    __tablename__ = "novels"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: 工作区目录，相对 data/ 根（如 `workspaces/{project_id}`）
    workspace_dir: Mapped[str] = mapped_column(String(300), nullable=False)
    #: 大纲相对路径，重命名后同步更新，是大纲唯一寻址依据
    outline_file: Mapped[str] = mapped_column(String(300), nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    genre: Mapped[str | None] = mapped_column(String(50), nullable=True)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: active | deleted（软删除）
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class ChapterRevision(Base):
    """章节修订快照。自动保存不快照（防版本爆炸）。"""

    __tablename__ = "chapter_revisions"
    __table_args__ = (Index("ix_chapter_revisions_chapter_id_version", "chapter_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    novel_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: manual | agent
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class ReadingProgress(Base):
    """阅读进度：单用户单行/项目。"""

    __tablename__ = "reading_progress"

    novel_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chapter_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scroll_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class AuditLog(Base):
    """审计日志：数据表 + 只读查询接口，不做后台管理 UI；同时 JSONL 备份。"""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_entity", "entity_type", "entity_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: JSON 文本（detail_json）
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class ProjectIndex(Base):
    """项目统计缓存 + 脏标记（dirty=1 表示派生索引待重建）。"""

    __tablename__ = "project_index"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chapter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_words: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_updated: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dirty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class UserSettings(Base):
    """通用设置：单行表（id 固定 'default'）。"""

    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="default")
    #: light | dark | system
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    primary_color: Mapped[str] = mapped_column(String(20), nullable=False, default="#409EFF")
    font_size: Mapped[int] = mapped_column(Integer, nullable=False, default=18)
    font_family: Mapped[str] = mapped_column(String(50), nullable=False, default="serif")
    reading_width: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    autosave_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    #: ★回落城市（浏览器定位失败/被拒时使用），不是定位结果
    city: Mapped[str] = mapped_column(String(60), nullable=False, default="北京")
    widget_enabled_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    pet_idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)


class LlmProvider(Base, TimestampMixin):
    """LLM Provider。`api_key_env` 仅存环境变量名，密钥本身只在 `.env`。"""

    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: openai | openai_compatible | anthropic | google | azure_openai | openrouter
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(300), nullable=True)
    api_key_env: Mapped[str | None] = mapped_column(String(80), nullable=True)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class LlmModel(Base, TimestampMixin):
    __tablename__ = "llm_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    max_context: Mapped[int] = mapped_column(Integer, nullable=False, default=32768)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 有序 model_ref 列表，供 ModelFallbackMiddleware 使用（M3）
    fallback_chain_json: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    content_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: streaming|complete|error|cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="complete")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
    """一次「用户发消息 → Agent 跑完」的运行（含门禁字段）。"""

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
    fallback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(String(32), nullable=False, default=utcnow)
    ended_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: G0|G1|G2|G3|NULL（非生成类运行为空）
    gate_stage: Mapped[str | None] = mapped_column(String(8), nullable=True)
    gate_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gate_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 门禁生成目标章（gate 运行专用）
    target_chapter_id: Mapped[str | None] = mapped_column(String(16), nullable=True)


class AgentInterrupt(Base):
    """中断：approval（工具审批）| consult（门禁咨询卡片）。"""

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


#: 供 Alembic / `create_all` 发现所有表
ALL_MODELS = [
    Novel,
    ChapterRevision,
    ReadingProgress,
    AuditLog,
    ProjectIndex,
    UserSettings,
    LlmProvider,
    LlmModel,
    AgentSession,
    Message,
    ToolCallLog,
    AgentRun,
    AgentInterrupt,
    SubagentConfig,
    SkillConfig,
    McpServer,
]
