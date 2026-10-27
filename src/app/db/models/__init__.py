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

    按域拆分：`novel.py`（内容）/ `agent.py`（Agent 运行与配置）/ `llm.py`（Provider/Model）/
    `config.py`（用户设置与审计）。本文件只做 re-export + `ALL_MODELS` 汇总，
    故 `from app.db.models import X` 的既有写法（含 alembic env.py）不受影响。
"""

from __future__ import annotations

from app.db.models.agent import (
    AgentInterrupt,
    AgentRun,
    AgentSession,
    McpServer,
    Message,
    SkillConfig,
    SubagentConfig,
    ToolCallLog,
)
from app.db.models.config import (
    AuditLog,
    UserSettings,
)
from app.db.models.llm import (
    LlmModel,
    LlmProvider,
)
from app.db.models.novel import (
    ChapterRevision,
    Novel,
    ProjectIndex,
    ReadingProgress,
)

#: 供 Alembic / `create_all` 发现所有表（顺序保持与拆分前一致）
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

__all__ = [
    "AgentInterrupt",
    "AgentRun",
    "AgentSession",
    "AuditLog",
    "ChapterRevision",
    "LlmModel",
    "LlmProvider",
    "McpServer",
    "Message",
    "Novel",
    "ProjectIndex",
    "ReadingProgress",
    "SkillConfig",
    "SubagentConfig",
    "ToolCallLog",
    "UserSettings",
    "ALL_MODELS",
]
