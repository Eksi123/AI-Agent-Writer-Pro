# -*- coding: utf-8 -*-
"""
模块: app.schemas.llm
文件名: llm.py
作者: xionggaoqing
时间: 2026-10-27
说明: LLM Provider / Model DTO（手册「二、(1)」+「六 → 路由树 · 模型」）。

    ★密钥不入 DB：只存 `api_key_env`（环境变量名），响应回 `api_key_masked`。

    响应统一由 `core.response.ok()` 包成 `{code,message,data}`，此处只定义 `data` 部分。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# LLM Provider / Model（M3）
# ---------------------------------------------------------------------------

_PROVIDER_TYPES = ("openai", "openai_compatible", "anthropic", "google", "azure_openai", "openrouter")


class LlmProviderCreate(BaseModel):
    name: str = Field(max_length=100, description="Provider 名称，必填")
    type: Literal[_PROVIDER_TYPES] = Field(description="厂商类型")  # type: ignore[valid-type]
    base_url: str | None = Field(default=None, max_length=300)
    #: 只存变量名；明文经 api_key_value 写 .env
    api_key_env: str | None = Field(default=None, max_length=80)
    api_key_value: str | None = Field(default=None, description="明文密钥，仅写 .env 不落库")
    extra: dict[str, Any] | None = None
    enabled: bool = True
    priority: int = 0


class LlmProviderUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    type: Literal[_PROVIDER_TYPES] | None = None  # type: ignore[valid-type]
    base_url: str | None = Field(default=None, max_length=300)
    api_key_env: str | None = Field(default=None, max_length=80)
    api_key_value: str | None = None
    clear_api_key: bool = False
    extra: dict[str, Any] | None = None
    enabled: bool | None = None
    priority: int | None = None


class LlmModelCreate(BaseModel):
    provider_id: str
    model_id: str = Field(max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    max_context: int = Field(default=32768, ge=1024, le=2_000_000)
    supports_tools: bool = True
    default_params: dict[str, Any] | None = None
    is_default: bool = False
    enabled: bool = True
    #: 有序 model_ref 列表（"{provider_id}:{model_id}"）
    fallback_chain: list[str] = Field(default_factory=list)


class LlmModelUpdate(BaseModel):
    model_id: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    max_context: int | None = Field(default=None, ge=1024, le=2_000_000)
    supports_tools: bool | None = None
    default_params: dict[str, Any] | None = None
    is_default: bool | None = None
    enabled: bool | None = None
    fallback_chain: list[str] | None = None


class LlmProviderTestIn(BaseModel):
    model_id: str | None = Field(default=None, description="指定测试模型；空则取该 Provider 首个可用模型")
