# -*- coding: utf-8 -*-
"""
模块: app.schemas.agent
文件名: agent.py
作者: xionggaoqing
时间: 2026-10-27
说明: Agent 会话 / 消息 / 中断 / SSE 信封 DTO（手册「六 → 路由树 · Agent」+「二、(2) 流式」）。

    响应统一由 `core.response.ok()` 包成 `{code,message,data}`，此处只定义 `data` 部分。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Agent 会话 / 消息 / 运行 / 中断（M3）
# ---------------------------------------------------------------------------


class AgentSessionCreate(BaseModel):
    novel_id: str = Field(description="项目 id（会话按项目隔离）")
    title: str | None = Field(default=None, max_length=200)
    model_ref: str | None = Field(default=None, description="会话级默认模型；空则用全局默认")


class AgentSessionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class AgentSessionContext(BaseModel):
    """会话级上下文（模型 / 子代理 / 技能 / 上下文章节）。"""

    model_ref: str | None = None
    subagent_ids: list[str] | None = None
    skill_ids: list[str] | None = None
    context_chapter_ids: list[str] | None = None


class AgentMessageIn(BaseModel):
    """发消息 → 202 + {run_id}。"""

    content: str = Field(min_length=1, max_length=100_000)
    #: 单条消息级模型覆盖（仅本次 run 生效，不回写会话）
    model_ref: str | None = None
    #: 分支：指定父消息 id（编辑后重发 / 重新生成时用）；空则接在 active_leaf 之后
    parent_id: str | None = None
    #: 流程：显式指定生成章（v1101 起前端不再传，仅保留兼容）；空则由意图检测决定
    target_chapter_id: str | None = None
    #: v1101：用户当前正在编辑/阅读的章（意图命中且消息未点名章节时作为目标章）
    active_chapter_id: str | None = None
    #: ★v1101：隐藏上下文（如选区润色提示词）——随本轮一起送给模型，
    #: 以 system 消息注入 graph，**不入消息树、不在会话里展示**。
    hidden_context: str | None = Field(default=None, max_length=100_000)


class AgentBranchIn(BaseModel):
    """切换 active_leaf_id（分支 / 重新生成后查看历史版本）。"""

    message_id: str


class AgentSteerIn(BaseModel):
    """v1028：运行中「通过消息引导发送」——把引导文本注入正在跑的 run。"""

    content: str = Field(min_length=1, max_length=20_000)


class AgentInterruptResolveIn(BaseModel):
    """审批与咨询统一入口。

    - approval: `decision=approve|edit|reject|respond`（edit 带 edited_args，respond 带 message）
    - consult:  `decision=option|text`（option 带 choice，text 带 text）
    """

    decision: Literal["approve", "edit", "reject", "respond", "option", "text"]
    edited_args: dict[str, Any] | None = None
    message: str | None = None
    choice: str | None = None
    text: str | None = None


class SseEnvelopeOut(BaseModel):
    """SSE 事件信封（手册「六 · SSE 契约」）。"""

    event: str
    run_id: str
    session_id: str
    seq: int
    ts: str
    data: dict[str, Any] = Field(default_factory=dict)
