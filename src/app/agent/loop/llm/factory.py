# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.llm.factory
文件名: factory.py
作者: xionggaoqing
时间: 2026-10-27
说明: ChatModel 工厂：按 `llm_providers.type` 装配对应 LangChain ChatModel（手册「二、(1)」）。

    - 密钥经 `core.security.get_api_key(api_key_env)` 从 `.env`/环境读取，**不入 DB**；
    - `model_ref = "{provider_id}:{model_id}"`；解析后取 provider + model 行组装参数；
    - 未配置密钥 / provider 不存在 → `LlmProviderError`（502/50201）；
    - 工厂结果按 `(model_ref, config_version)` LRU 缓存，`POST /config/reload` 使 `config_version += 1` 失效。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop.llm.params import merge_params
from app.core.config import current_config_version
from app.core.errors import LlmProviderError, NotFoundError
from app.core.logging import get_logger
from app.core.security import get_api_key, mask
from app.db.models import LlmModel, LlmProvider

logger = get_logger(__name__)

#: 常见厂商的 openai_compatible base_url（type=openai_compatible 且 provider 未填 base_url 时的兜底）
_KNOWN_BASE_URLS = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "openrouter": "https://openrouter.ai/api/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}


async def resolve_model_ref(session: AsyncSession, model_ref: str) -> tuple[LlmProvider, LlmModel]:
    """`"{provider_id}:{model_id}"` → (provider 行, model 行)。"""
    if ":" not in model_ref:
        raise NotFoundError(f"非法 model_ref（应为 provider_id:model_id）：{model_ref}")
    provider_id, model_id = model_ref.split(":", 1)
    provider = await session.get(LlmProvider, provider_id)
    if provider is None:
        raise NotFoundError(f"Provider 不存在：{provider_id}")
    row = (
        await session.execute(
            select(LlmModel).where(LlmModel.provider_id == provider_id, LlmModel.model_id == model_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"模型不存在：{model_id}（provider={provider.name}）")
    return provider, row


def _base_url_for(provider: LlmProvider) -> str | None:
    if provider.base_url:
        return provider.base_url
    name = (provider.name or "").lower()
    for key, url in _KNOWN_BASE_URLS.items():
        if key in name:
            return url
    return None


def build_chat_model(
    provider: LlmProvider,
    model: LlmModel,
    *,
    extra_params: dict[str, Any] | None = None,
) -> BaseChatModel:
    """装配 ChatModel（不落 DB 的密钥现取现用）。

    Raises:
        LlmProviderError: 密钥缺失或类型不支持。
    """
    params = _params_of(model, extra_params, provider=provider)

    api_key = ""
    if provider.api_key_env:
        value, missing = get_api_key(provider.api_key_env)
        if missing or not value:
            raise LlmProviderError(
                f"Provider「{provider.name}」未配置密钥：环境变量 {provider.api_key_env} 为空",
                data={"provider": provider.name, "api_key_env": provider.api_key_env},
            )
        api_key = value

    ptype = provider.type
    try:
        if ptype in ("openai", "openai_compatible", "openrouter"):
            from langchain_openai import ChatOpenAI

            kwargs: dict[str, Any] = {"model": model.model_id, "api_key": api_key or None, **params}
            base_url = _base_url_for(provider)
            if ptype == "openrouter" and not base_url:
                base_url = _KNOWN_BASE_URLS["openrouter"]
            if base_url:
                kwargs["base_url"] = base_url
            return ChatOpenAI(**kwargs)
        if ptype == "azure_openai":
            from langchain_openai import AzureChatOpenAI

            extra = _extra_of(provider)
            return AzureChatOpenAI(
                azure_deployment=model.model_id,
                api_key=api_key or None,
                azure_endpoint=extra.get("azure_endpoint") or provider.base_url,
                api_version=extra.get("api_version", "2024-06-01"),
                **params,
            )
        if ptype == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(model=model.model_id, api_key=api_key or None, **params)
        if ptype == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(model=model.model_id, google_api_key=api_key or None, **params)
    except LlmProviderError:
        raise
    except Exception as exc:  # 依赖缺失 / 参数不兼容
        logger.error("chat_model_build_failed", provider=provider.name, model=model.model_id, error=str(exc))
        raise LlmProviderError(f"构建 ChatModel 失败（{provider.name}/{model.model_id}）：{exc}") from exc

    raise LlmProviderError(f"不支持的 provider 类型：{ptype}")


def _extra_of(provider: LlmProvider) -> dict[str, Any]:
    if not provider.extra_json:
        return {}
    try:
        return json.loads(provider.extra_json)
    except ValueError:
        return {}


def _params_of(
    model: LlmModel,
    extra_params: dict[str, Any] | None,
    *,
    provider: LlmProvider | None = None,
) -> dict[str, Any]:
    """Provider 参数（v1028：超时/温度/Max Token）+ default_params_json + 调用方覆盖合并。"""

    provider_layer: dict[str, Any] = {}
    if provider is not None:
        # v1028：大模型配置弹窗的超时时长 / Temperature / Max Token（除密钥外均入库）
        provider_layer = {
            "timeout": getattr(provider, "timeout_sec", None),
            "temperature": getattr(provider, "temperature", None),
            "max_tokens": getattr(provider, "max_tokens", None),
        }

    default: dict[str, Any] = {}
    if model.default_params_json:
        try:
            default = json.loads(model.default_params_json)
        except ValueError:
            default = {}
    merged = merge_params(provider_layer, default, extra_params)
    # ChatModel 构造参数名对齐（streaming → stream 由各 SDK 处理；extra_body 仅 openai 用）
    return merged


def provider_key_status(provider: LlmProvider) -> dict[str, Any]:
    """供前端「未配置密钥」警示 + 掩码展示。"""
    if not provider.api_key_env:
        return {"api_key_env": None, "configured": False, "masked": ""}
    value, missing = get_api_key(provider.api_key_env)
    return {
        "api_key_env": provider.api_key_env,
        "configured": not missing and bool(value),
        "masked": mask(value) if value else "",
    }


# ---- 工厂缓存（config_version 失效，防实例无限增长）----

_MODEL_CACHE: dict[tuple[str, int], BaseChatModel] = {}
_CACHE_LIMIT = 32

#: 测试注入缝：model_ref → 假模型（手册「十一」：Agent 测试用假模型，不打真实 LLM）
_MODEL_OVERRIDES: dict[str, Any] = {}


def register_model_override(model_ref: str, model: Any) -> None:
    """登记假模型（仅测试用）。"""
    _MODEL_OVERRIDES[model_ref] = model


def clear_model_overrides() -> None:
    _MODEL_OVERRIDES.clear()
    _MODEL_CACHE.clear()


def invalidate_model_cache() -> None:
    """`POST /config/reload`（config_version += 1）后调用，使缓存失效。"""
    _MODEL_CACHE.clear()


async def get_chat_model(
    session: AsyncSession,
    model_ref: str,
    *,
    extra_params: dict[str, Any] | None = None,
) -> BaseChatModel:
    """取 ChatModel（带缓存）。

    `extra_params` 非空时**绕过缓存**（单条消息级参数覆盖只对本次 run 生效）。
    测试登记的假模型优先（不打真实 LLM）。
    """
    if model_ref in _MODEL_OVERRIDES:
        return _MODEL_OVERRIDES[model_ref]

    version = current_config_version()
    key = (model_ref, version)

    if not extra_params and key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    provider, model = await resolve_model_ref(session, model_ref)
    chat_model = build_chat_model(provider, model, extra_params=extra_params)

    if not extra_params:
        if len(_MODEL_CACHE) >= _CACHE_LIMIT:
            _MODEL_CACHE.pop(next(iter(_MODEL_CACHE)), None)
        _MODEL_CACHE[key] = chat_model
    return chat_model


async def get_default_chat_model(
    session: AsyncSession, *, extra_params: dict[str, Any] | None = None
) -> tuple[str, BaseChatModel]:
    """取默认模型的 ChatModel（`llm_models.is_default`，多个时取 enabled+provider 优先级最高）。

    Returns:
        (model_ref, chat_model)

    Raises:
        LlmProviderError: 未配置任何默认模型（前端据此引导去「AI 工具」页配置）。
    """
    rows = (
        await session.execute(
            select(LlmModel, LlmProvider)
            .join(LlmProvider, LlmProvider.id == LlmModel.provider_id)
            .where(LlmModel.enabled.is_(True), LlmProvider.enabled.is_(True))
            .order_by(
                LlmModel.is_default.desc(),
                LlmProvider.priority.desc(),
                LlmModel.created_at.asc(),
            )
        )
    ).all()
    if not rows:
        raise LlmProviderError(
            "尚未配置任何可用的 LLM Provider/模型，请先在「AI 工具」页添加",
            data={"hint": "POST /api/v1/llm/providers"},
        )
    model, provider = rows[0]
    ref = f"{provider.id}:{model.model_id}"
    # 经 get_chat_model 以复用测试注入缝与缓存
    return ref, await get_chat_model(session, ref, extra_params=extra_params)


async def get_fallback_chain(session: AsyncSession, model_ref: str) -> list[str]:
    """降级链：`llm_models.fallback_chain_json`（有序 model_ref 列表）；空则只含自身。"""
    _provider, model = await resolve_model_ref(session, model_ref)
    chain = [model_ref]
    if model.fallback_chain_json:
        try:
            parsed = json.loads(model.fallback_chain_json)
        except ValueError:
            parsed = []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str) and item and item not in chain:
                    chain.append(item)
    return chain
