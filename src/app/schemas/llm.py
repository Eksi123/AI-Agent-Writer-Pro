# -*- coding: utf-8 -*-
"""
模块: app.schemas.llm
文件名: llm.py
作者: xionggaoqing
时间: 2026-10-27
说明: LLM Provider / Model DTO（手册「二、(1)」+「六 → 路由树 · 模型」）。

    密钥不入 DB：只存 `api_key_env`（环境变量名），响应回 `api_key_masked`。

    响应统一由 `core.response.ok()` 包成 `{code,message,data}`，此处只定义 `data` 部分。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# LLM Provider / Model（M3）
# ---------------------------------------------------------------------------

_PROVIDER_TYPES = ("openai", "openai_compatible", "anthropic", "google", "azure_openai", "openrouter")

#: 协议类型 → temperature 合法范围
_TEMPERATURE_RANGES: dict[str, tuple[float, float]] = {
    "openai": (0.0, 2.0),
    "openai_compatible": (0.0, 2.0),
    "openrouter": (0.0, 2.0),
    "azure_openai": (0.0, 2.0),
    "anthropic": (0.0, 1.0),
    "google": (0.0, 2.0),
}


def validate_provider_params(type_: str, temperature: float, timeout_sec: int, max_tokens: int) -> None:
    """校验大模型必填参数。不合法抛 `ValueError`（API 层转 40001）。"""
    if not (10 <= timeout_sec <= 60):
        raise ValueError(f"超时时长须在 10~60 秒之间，收到：{timeout_sec}")
    lo, hi = _TEMPERATURE_RANGES.get(type_, (0.0, 2.0))
    # 0 不允许（不同协议取值范围不含 0）
    if not (lo < temperature <= hi):
        raise ValueError(f"Temperature 须在 ({lo}, {hi}] 范围内（不包括 0），收到：{temperature}")
    if max_tokens < 1:
        raise ValueError(f"Max Token 须为正整数，收到：{max_tokens}")


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
    #: 必填参数：超时（秒，10~60，默认 30）
    timeout_sec: int = Field(default=30, ge=10, le=60)
    #: 必填参数：温度（按协议类型校验范围，默认 1.0）
    temperature: float = Field(default=1.0, gt=0, le=2)
    #: 必填参数：最大输出 token（默认 32K，不设上限）
    max_tokens: int = Field(default=32768, ge=1)


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
    timeout_sec: int | None = Field(default=None, ge=10, le=60)
    temperature: float | None = Field(default=None, gt=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)


class LlmModelCreate(BaseModel):
    provider_id: str
    model_id: str = Field(max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    max_context: int = Field(default=262_144, ge=102_400, le=1_048_576)
    supports_tools: bool = True
    default_params: dict[str, Any] | None = None
    is_default: bool = False
    enabled: bool = True


class LlmModelUpdate(BaseModel):
    model_id: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    max_context: int | None = Field(default=None, ge=102_400, le=1_048_576)
    supports_tools: bool | None = None
    default_params: dict[str, Any] | None = None
    is_default: bool | None = None
    enabled: bool | None = None


class LlmProviderTestIn(BaseModel):
    model_id: str | None = Field(default=None, description="指定测试模型；空则取该 Provider 首个可用模型")
