# -*- coding: utf-8 -*-
"""
模块: app.db.models.llm
文件名: llm.py
作者: xionggaoqing
时间: 2026-10-27
说明: LLM 域 ORM：llm_providers / llm_models（手册「五、数据模型」M3）。

    密钥**不入 DB**：只存 `api_key_env`（环境变量名），读取经 `core.security`。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_id


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
    #: 大模型级常用参数（超时秒 / 温度 / 最大输出 token）
    timeout_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=32768)


class LlmModel(Base, TimestampMixin):
    __tablename__ = "llm_models"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    max_context: Mapped[int] = mapped_column(Integer, nullable=False, default=262_144)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: 已废弃：降级链移除（改为连续失败自动切换备用模型）；列保留兼容旧库
    fallback_chain_json: Mapped[str | None] = mapped_column(Text, nullable=True)
