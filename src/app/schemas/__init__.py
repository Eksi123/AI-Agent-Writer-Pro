# -*- coding: utf-8 -*-
"""
模块: app.schemas
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: Pydantic DTO：请求体与响应 `data` 的结构定义。

    响应统一由 `core.response.ok()` 包成 `{code,message,data}`，此处只定义 `data` 部分。

    按域拆分：`novel.py`（项目/卷章/大纲）、`agent.py`（会话/消息/中断/SSE）、
    `llm.py`（Provider/Model）、`config.py`（设置/导出/检索/健康）。
    本文件只做 re-export，故 `from app.schemas import X` 的既有写法不受影响。
"""

from __future__ import annotations

from app.schemas.agent import (
    AgentBranchIn,
    AgentInterruptResolveIn,
    AgentMessageIn,
    AgentSessionContext,
    AgentSessionCreate,
    AgentSessionUpdate,
    AgentSteerIn,
    SseEnvelopeOut,
)
from app.schemas.config import (
    ExportTaskOut,
    HealthOut,
    SearchOut,
    SearchResultItem,
    SettingsIn,
    SettingsOut,
)
from app.schemas.llm import (
    LlmModelCreate,
    LlmModelUpdate,
    LlmProviderCreate,
    LlmProviderTestIn,
    LlmProviderUpdate,
)
from app.schemas.novel import (
    ChapterCreate,
    ChapterOut,
    ChapterUpdate,
    ContentIn,
    ContentOut,
    ContentWriteResult,
    NovelCreate,
    NovelDuplicate,
    NovelOut,
    NovelUpdate,
    OutlineIn,
    OutlineIssue,
    OutlineOut,
    OutlineValidateOut,
    ReadingProgressIn,
    ReadingProgressOut,
    RevisionOut,
    VolumeCreate,
    VolumeOut,
    VolumeUpdate,
)

__all__ = [
    "AgentBranchIn",
    "AgentSteerIn",
    "AgentInterruptResolveIn",
    "AgentMessageIn",
    "AgentSessionContext",
    "AgentSessionCreate",
    "AgentSessionUpdate",
    "ChapterCreate",
    "ChapterOut",
    "ChapterUpdate",
    "ContentIn",
    "ContentOut",
    "ContentWriteResult",
    "ExportTaskOut",
    "HealthOut",
    "LlmModelCreate",
    "LlmModelUpdate",
    "LlmProviderCreate",
    "LlmProviderTestIn",
    "LlmProviderUpdate",
    "NovelCreate",
    "NovelDuplicate",
    "NovelOut",
    "NovelUpdate",
    "OutlineIn",
    "OutlineIssue",
    "OutlineOut",
    "OutlineValidateOut",
    "ReadingProgressIn",
    "ReadingProgressOut",
    "RevisionOut",
    "SearchOut",
    "SearchResultItem",
    "SettingsIn",
    "SettingsOut",
    "SseEnvelopeOut",
    "VolumeCreate",
    "VolumeOut",
    "VolumeUpdate",
]
