# -*- coding: utf-8 -*-
"""
模块: app.services.llm
文件名: llm.py
作者: xionggaoqing
时间: 2026-10-27
说明: LLM Provider / Model 管理服务（手册「三、(1) services」+「二、(1) 密钥安全」）。

    - 密钥**只写 `.env`**（`core.security.set_env_key`，白名单 + 原子写 + 备份），DB 只存 `api_key_env`；
    - 对外返回一律掩码（`sk-****abcd`），绝不回显明文；
    - 连接测试用极小请求（1 token），失败给可读错误；
    - Provider/Model 变更后触发配置重载（`config_version += 1` 使 Agent 工厂缓存失效）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm.factory import (
    build_chat_model,
    get_fallback_chain,
    invalidate_model_cache,
    provider_key_status,
)
from app.core.config import get_settings, reload_settings
from app.core.errors import BadRequestError, LlmProviderError, NotFoundError
from app.core.logging import get_logger
from app.core.security import get_api_key, is_whitelisted_key, mask, set_env_key
from app.db.base import new_id, utcnow
from app.db.models import LlmModel, LlmProvider
from app.services import audit

logger = get_logger(__name__)

#: 手册「二、(1) 多厂商适配」支持的类型
PROVIDER_TYPES = (
    "openai",
    "openai_compatible",
    "anthropic",
    "google",
    "azure_openai",
    "openrouter",
)

#: 常用环境变量名建议（前端下拉；均为白名单键）
SUGGESTED_KEY_ENVS = (
    "OPENAI_API_KEY",
    "DASHSCOPE_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "ZHIPU_API_KEY",
    "SILICONFLOW_API_KEY",
)


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


def provider_to_dto(row: LlmProvider, *, models: list[LlmModel] | None = None) -> dict[str, Any]:
    key_status = provider_key_status(row)
    extra: dict[str, Any] = {}
    if row.extra_json:
        try:
            extra = json.loads(row.extra_json)
        except ValueError:
            extra = {}
    dto: dict[str, Any] = {
        "id": row.id,
        "name": row.name,
        "type": row.type,
        "base_url": row.base_url,
        "api_key_env": row.api_key_env,
        "api_key_configured": key_status["configured"],
        "api_key_masked": key_status["masked"],
        "extra": extra,
        "enabled": row.enabled,
        "priority": row.priority,
        "timeout_sec": getattr(row, "timeout_sec", 30),
        "temperature": getattr(row, "temperature", 1.0),
        "max_tokens": getattr(row, "max_tokens", 2048),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
    if models is not None:
        dto["models"] = [model_to_dto(m) for m in models]
    return dto


def model_to_dto(row: LlmModel) -> dict[str, Any]:
    default_params: dict[str, Any] = {}
    if row.default_params_json:
        try:
            default_params = json.loads(row.default_params_json)
        except ValueError:
            default_params = {}
    fallback_chain: list[str] = []
    if row.fallback_chain_json:
        try:
            parsed = json.loads(row.fallback_chain_json)
            fallback_chain = [x for x in parsed if isinstance(x, str)] if isinstance(parsed, list) else []
        except ValueError:
            fallback_chain = []
    return {
        "id": row.id,
        "provider_id": row.provider_id,
        "model_ref": f"{row.provider_id}:{row.model_id}",
        "model_id": row.model_id,
        "display_name": row.display_name,
        "max_context": row.max_context,
        "supports_tools": row.supports_tools,
        "default_params": default_params,
        "is_default": row.is_default,
        "enabled": row.enabled,
        "fallback_chain": fallback_chain,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------


def _check_params(type_: str, *, temperature: float, timeout_sec: int, max_tokens: int) -> None:
    """v1028：大模型必填参数校验（协议类型决定 temperature 取值范围）。"""
    from app.schemas.llm import validate_provider_params

    try:
        validate_provider_params(type_, temperature, timeout_sec, max_tokens)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


async def list_providers(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        (await session.execute(select(LlmProvider).order_by(LlmProvider.priority.desc(), LlmProvider.created_at.asc())))
        .scalars()
        .all()
    )
    result = []
    for row in rows:
        models = (
            (
                await session.execute(
                    select(LlmModel).where(LlmModel.provider_id == row.id).order_by(LlmModel.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        result.append(provider_to_dto(row, models=list(models)))
    return result


async def create_provider(
    session: AsyncSession,
    *,
    name: str,
    type: str,  # noqa: A002 - 与手册字段名一致
    base_url: str | None = None,
    api_key_env: str | None = None,
    api_key_value: str | None = None,
    extra: dict[str, Any] | None = None,
    enabled: bool = True,
    priority: int = 0,
    timeout_sec: int = 30,
    temperature: float = 1.0,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise BadRequestError("Provider 名称不能为空")
    if type not in PROVIDER_TYPES:
        raise BadRequestError(f"不支持的 provider 类型：{type}（可选：{', '.join(PROVIDER_TYPES)}）")
    _check_params(type, temperature=temperature, timeout_sec=timeout_sec, max_tokens=max_tokens)

    duplicate = (
        await session.execute(select(LlmProvider).where(LlmProvider.name == name))
    ).scalar_one_or_none()
    if duplicate is not None:
        raise BadRequestError(f"Provider 名称已存在：{name}")

    # 密钥：给了明文就写 .env（DB 只记变量名）
    env_name = (api_key_env or "").strip() or None
    if api_key_value:
        env_name = env_name or _derive_env_name(name)
        _write_secret(session, env_name, api_key_value)
    if env_name and not is_whitelisted_key(env_name):
        raise BadRequestError(f"环境变量名 {env_name} 不在密钥白名单内（须以 _API_KEY/_KEY/_TOKEN 结尾）")

    row = LlmProvider(
        id=new_id(),
        name=name,
        type=type,
        base_url=(base_url or "").strip() or None,
        api_key_env=env_name,
        extra_json=json.dumps(extra, ensure_ascii=False) if extra else None,
        enabled=enabled,
        priority=priority,
        timeout_sec=int(timeout_sec),
        temperature=float(temperature),
        max_tokens=int(max_tokens),
    )
    session.add(row)
    await session.flush()

    await audit.record(
        session,
        "llm.provider.create",
        entity_type="llm_provider",
        entity_id=row.id,
        detail={"name": name, "type": type, "api_key_env": env_name, "api_key_masked": mask(api_key_value)},
    )
    invalidate_model_cache()
    logger.info("llm_provider_created", id=row.id, name=name, type=type)
    return provider_to_dto(row, models=[])


async def update_provider(
    session: AsyncSession,
    provider_id: str,
    *,
    name: str | None = None,
    type: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    api_key_value: str | None = None,
    clear_api_key: bool = False,
    extra: dict[str, Any] | None = None,
    enabled: bool | None = None,
    priority: int | None = None,
    timeout_sec: int | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    row = await session.get(LlmProvider, provider_id)
    if row is None:
        raise NotFoundError(f"Provider 不存在：{provider_id}")

    changes: dict[str, Any] = {}

    if name is not None:
        name = name.strip()
        if not name:
            raise BadRequestError("Provider 名称不能为空")
        if name != row.name:
            dup = (
                await session.execute(
                    select(LlmProvider).where(LlmProvider.name == name, LlmProvider.id != provider_id)
                )
            ).scalar_one_or_none()
            if dup is not None:
                raise BadRequestError(f"Provider 名称已存在：{name}")
            changes["name"] = {"from": row.name, "to": name}
            row.name = name

    if type is not None:
        if type not in PROVIDER_TYPES:
            raise BadRequestError(f"不支持的 provider 类型：{type}")
        if type != row.type:
            # 协议类型变更后，既有 temperature 必须仍在新协议取值范围内（v1028）
            _check_params(
                type,
                temperature=float(temperature) if temperature is not None else float(getattr(row, "temperature", 1.0)),
                timeout_sec=int(timeout_sec) if timeout_sec is not None else int(getattr(row, "timeout_sec", 30)),
                max_tokens=int(max_tokens) if max_tokens is not None else int(getattr(row, "max_tokens", 2048)),
            )
            changes["type"] = {"from": row.type, "to": type}
            row.type = type

    if base_url is not None:
        new_url = base_url.strip() or None
        if new_url != row.base_url:
            changes["base_url"] = {"from": row.base_url, "to": new_url}
            row.base_url = new_url

    if clear_api_key and row.api_key_env:
        changes["api_key_env"] = {"from": row.api_key_env, "to": None}
        row.api_key_env = None
    elif api_key_value:
        env_name = (api_key_env or "").strip() or row.api_key_env or _derive_env_name(row.name)
        if not is_whitelisted_key(env_name):
            raise BadRequestError(f"环境变量名 {env_name} 不在密钥白名单内")
        _write_secret(session, env_name, api_key_value)
        changes["api_key_env"] = {"from": row.api_key_env, "to": env_name}
        changes["api_key"] = {"masked": mask(api_key_value)}
        row.api_key_env = env_name
    elif api_key_env is not None:
        env_name = api_key_env.strip() or None
        if env_name and not is_whitelisted_key(env_name):
            raise BadRequestError(f"环境变量名 {env_name} 不在密钥白名单内")
        if env_name != row.api_key_env:
            changes["api_key_env"] = {"from": row.api_key_env, "to": env_name}
            row.api_key_env = env_name

    if extra is not None:
        new_json = json.dumps(extra, ensure_ascii=False) if extra else None
        if new_json != row.extra_json:
            changes["extra"] = {"from": row.extra_json, "to": extra}
            row.extra_json = new_json

    if enabled is not None and enabled != row.enabled:
        changes["enabled"] = {"from": row.enabled, "to": enabled}
        row.enabled = enabled

    if priority is not None and priority != row.priority:
        changes["priority"] = {"from": row.priority, "to": priority}
        row.priority = priority

    # v1028：超时 / Temperature / Max Token（校验以最终生效的 type 为准）
    if timeout_sec is not None or temperature is not None or max_tokens is not None:
        eff_type = type or row.type
        eff_temp = float(temperature) if temperature is not None else float(getattr(row, "temperature", 1.0))
        eff_timeout = int(timeout_sec) if timeout_sec is not None else int(getattr(row, "timeout_sec", 30))
        eff_max = int(max_tokens) if max_tokens is not None else int(getattr(row, "max_tokens", 2048))
        _check_params(eff_type, temperature=eff_temp, timeout_sec=eff_timeout, max_tokens=eff_max)
        if timeout_sec is not None and timeout_sec != row.timeout_sec:
            changes["timeout_sec"] = {"from": row.timeout_sec, "to": timeout_sec}
            row.timeout_sec = int(timeout_sec)
        if temperature is not None and float(temperature) != float(row.temperature):
            changes["temperature"] = {"from": row.temperature, "to": temperature}
            row.temperature = float(temperature)
        if max_tokens is not None and max_tokens != row.max_tokens:
            changes["max_tokens"] = {"from": row.max_tokens, "to": max_tokens}
            row.max_tokens = int(max_tokens)

    if changes:
        row.updated_at = utcnow()
        await session.flush()
        await audit.record(
            session, "llm.provider.update", entity_type="llm_provider", entity_id=provider_id, detail=changes
        )
        invalidate_model_cache()

    models = (
        (
            await session.execute(
                select(LlmModel).where(LlmModel.provider_id == provider_id).order_by(LlmModel.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return provider_to_dto(row, models=list(models))


async def delete_provider(session: AsyncSession, provider_id: str) -> dict[str, Any]:
    row = await session.get(LlmProvider, provider_id)
    if row is None:
        raise NotFoundError(f"Provider 不存在：{provider_id}")

    models = (
        (await session.execute(select(LlmModel).where(LlmModel.provider_id == provider_id))).scalars().all()
    )
    for m in models:
        await session.delete(m)
    await session.delete(row)
    await session.flush()

    await audit.record(
        session,
        "llm.provider.delete",
        entity_type="llm_provider",
        entity_id=provider_id,
        detail={"name": row.name, "models_deleted": len(models)},
    )
    invalidate_model_cache()
    return {"id": provider_id, "deleted": True, "models_deleted": len(models)}


# ---------------------------------------------------------------------------
# Model CRUD
# ---------------------------------------------------------------------------


async def list_models(session: AsyncSession, *, provider_id: str | None = None) -> list[dict[str, Any]]:
    stmt = select(LlmModel).order_by(LlmModel.is_default.desc(), LlmModel.created_at.asc())
    if provider_id:
        stmt = stmt.where(LlmModel.provider_id == provider_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [model_to_dto(r) for r in rows]


async def create_model(
    session: AsyncSession,
    *,
    provider_id: str,
    model_id: str,
    display_name: str | None = None,
    max_context: int = 32768,
    supports_tools: bool = True,
    default_params: dict[str, Any] | None = None,
    is_default: bool = False,
    enabled: bool = True,
    fallback_chain: list[str] | None = None,
) -> dict[str, Any]:
    provider = await session.get(LlmProvider, provider_id)
    if provider is None:
        raise NotFoundError(f"Provider 不存在：{provider_id}")

    model_id = (model_id or "").strip()
    if not model_id:
        raise BadRequestError("model_id 不能为空")

    dup = (
        await session.execute(
            select(LlmModel).where(LlmModel.provider_id == provider_id, LlmModel.model_id == model_id)
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise BadRequestError(f"该 Provider 下模型已存在：{model_id}")

    if is_default:
        await _clear_other_defaults(session, None)

    row = LlmModel(
        id=new_id(),
        provider_id=provider_id,
        model_id=model_id,
        display_name=(display_name or "").strip() or None,
        max_context=int(max_context or 32768),
        supports_tools=supports_tools,
        default_params_json=json.dumps(default_params, ensure_ascii=False) if default_params else None,
        is_default=is_default,
        enabled=enabled,
        fallback_chain_json=_dump_chain(fallback_chain, provider_id, model_id),
    )
    session.add(row)
    await session.flush()

    await audit.record(
        session,
        "llm.model.create",
        entity_type="llm_model",
        entity_id=row.id,
        detail={"provider": provider.name, "model_id": model_id, "is_default": is_default},
    )
    invalidate_model_cache()
    return model_to_dto(row)


async def update_model(
    session: AsyncSession,
    model_pk: str,
    *,
    model_id: str | None = None,
    display_name: str | None = None,
    max_context: int | None = None,
    supports_tools: bool | None = None,
    default_params: dict[str, Any] | None = None,
    is_default: bool | None = None,
    enabled: bool | None = None,
    fallback_chain: list[str] | None = None,
) -> dict[str, Any]:
    row = await session.get(LlmModel, model_pk)
    if row is None:
        raise NotFoundError(f"模型不存在：{model_pk}")

    changes: dict[str, Any] = {}

    if model_id is not None:
        model_id = model_id.strip()
        if not model_id:
            raise BadRequestError("model_id 不能为空")
        if model_id != row.model_id:
            dup = (
                await session.execute(
                    select(LlmModel).where(
                        LlmModel.provider_id == row.provider_id,
                        LlmModel.model_id == model_id,
                        LlmModel.id != model_pk,
                    )
                )
            ).scalar_one_or_none()
            if dup is not None:
                raise BadRequestError(f"该 Provider 下模型已存在：{model_id}")
            changes["model_id"] = {"from": row.model_id, "to": model_id}
            row.model_id = model_id

    if display_name is not None:
        new_name = display_name.strip() or None
        if new_name != row.display_name:
            changes["display_name"] = {"from": row.display_name, "to": new_name}
            row.display_name = new_name

    if max_context is not None and int(max_context) != row.max_context:
        changes["max_context"] = {"from": row.max_context, "to": int(max_context)}
        row.max_context = int(max_context)

    if supports_tools is not None and supports_tools != row.supports_tools:
        changes["supports_tools"] = {"from": row.supports_tools, "to": supports_tools}
        row.supports_tools = supports_tools

    if default_params is not None:
        new_json = json.dumps(default_params, ensure_ascii=False) if default_params else None
        if new_json != row.default_params_json:
            changes["default_params"] = {"from": row.default_params_json, "to": default_params}
            row.default_params_json = new_json

    if is_default is not None and is_default != row.is_default:
        changes["is_default"] = {"from": row.is_default, "to": is_default}
        row.is_default = is_default
        if is_default:
            await _clear_other_defaults(session, model_pk)

    if enabled is not None and enabled != row.enabled:
        changes["enabled"] = {"from": row.enabled, "to": enabled}
        row.enabled = enabled

    if fallback_chain is not None:
        new_json = _dump_chain(fallback_chain, row.provider_id, row.model_id)
        if new_json != row.fallback_chain_json:
            changes["fallback_chain"] = {"from": row.fallback_chain_json, "to": fallback_chain}
            row.fallback_chain_json = new_json

    if changes:
        row.updated_at = utcnow()
        await session.flush()
        await audit.record(session, "llm.model.update", entity_type="llm_model", entity_id=model_pk, detail=changes)
        invalidate_model_cache()

    return model_to_dto(row)


async def delete_model(session: AsyncSession, model_pk: str) -> dict[str, Any]:
    row = await session.get(LlmModel, model_pk)
    if row is None:
        raise NotFoundError(f"模型不存在：{model_pk}")
    detail = {"provider_id": row.provider_id, "model_id": row.model_id, "was_default": row.is_default}
    await session.delete(row)
    await session.flush()
    await audit.record(session, "llm.model.delete", entity_type="llm_model", entity_id=model_pk, detail=detail)
    invalidate_model_cache()
    return {"id": model_pk, "deleted": True}


# ---------------------------------------------------------------------------
# 连接测试
# ---------------------------------------------------------------------------


async def test_provider(session: AsyncSession, provider_id: str, *, model_id: str | None = None) -> dict[str, Any]:
    """极小请求连接测试（1 token）。返回 ok/latency_ms/model/message。"""
    import time

    provider = await session.get(LlmProvider, provider_id)
    if provider is None:
        raise NotFoundError(f"Provider 不存在：{provider_id}")

    if provider.api_key_env:
        value, missing = get_api_key(provider.api_key_env)
        if missing or not value:
            return {
                "ok": False,
                "provider": provider.name,
                "message": f"环境变量 {provider.api_key_env} 未配置密钥（请在 .env 或此处填写）",
                "api_key_env": provider.api_key_env,
            }

    # 选测试模型：显式指定 > 该 provider 下 enabled 的第一个
    model_row: LlmModel | None = None
    if model_id:
        model_row = (
            await session.execute(
                select(LlmModel).where(LlmModel.provider_id == provider_id, LlmModel.model_id == model_id)
            )
        ).scalar_one_or_none()
        if model_row is None:
            raise NotFoundError(f"模型不存在：{model_id}")
    else:
        model_row = (
            await session.execute(
                select(LlmModel).where(LlmModel.provider_id == provider_id, LlmModel.enabled.is_(True))
            )
        ).scalars().first()

    if model_row is None:
        return {
            "ok": False,
            "provider": provider.name,
            "message": "该 Provider 下没有可用模型，请先添加模型再测试",
        }

    try:
        chat_model = build_chat_model(provider, model_row, extra_params={"max_tokens": 4, "temperature": 0.0})
    except LlmProviderError as exc:
        return {"ok": False, "provider": provider.name, "model": model_row.model_id, "message": exc.message}

    started = time.perf_counter()
    try:
        from langchain_core.messages import HumanMessage

        resp = await chat_model.ainvoke([HumanMessage(content="ping")])
        latency = int((time.perf_counter() - started) * 1000)
        text = getattr(resp, "content", "")
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False, default=str)
        await audit.record(
            session,
            "llm.provider.test",
            entity_type="llm_provider",
            entity_id=provider_id,
            detail={"model": model_row.model_id, "ok": True, "latency_ms": latency},
        )
        return {
            "ok": True,
            "provider": provider.name,
            "model": model_row.model_id,
            "latency_ms": latency,
            "reply_excerpt": text[:80],
            "message": "连接成功",
        }
    except Exception as exc:  # noqa: BLE001 - 任何网络/鉴权异常都转成可读结果
        latency = int((time.perf_counter() - started) * 1000)
        message = f"{type(exc).__name__}: {exc}"
        logger.warning("llm_provider_test_failed", provider=provider.name, error=message)
        await audit.record(
            session,
            "llm.provider.test",
            entity_type="llm_provider",
            entity_id=provider_id,
            detail={"model": model_row.model_id, "ok": False, "error": message[:500]},
        )
        return {
            "ok": False,
            "provider": provider.name,
            "model": model_row.model_id,
            "latency_ms": latency,
            "message": message[:500],
        }


# ---------------------------------------------------------------------------
# 配置重载
# ---------------------------------------------------------------------------


async def reload_config(session: AsyncSession) -> dict[str, Any]:
    """重读 `.env` + `config_version += 1`，使 Agent 工厂与模型缓存失效。"""
    from app.core.config import current_config_version

    settings = reload_settings()  # 内部已 bump config_version
    version = current_config_version()
    invalidate_model_cache()

    from app.agent.tracing import reset_client

    reset_client()

    await audit.record(
        session,
        "config.reload",
        entity_type="config",
        entity_id="default",
        detail={"config_version": version},
    )
    logger.info("config_reloaded", config_version=version)
    return {
        "config_version": version,
        "langfuse_enabled": settings.langfuse_enabled,
        "provider_keys": _provider_key_overview(),
    }


def _provider_key_overview() -> dict[str, bool]:
    from app.core.security import configured_provider_keys

    return configured_provider_keys()


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _derive_env_name(name: str) -> str:
    """由 Provider 名派生环境变量名（大写 + 下划线 + `_API_KEY` 后缀，符合白名单）。"""
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper() or "CUSTOM"
    candidate = f"{slug}_API_KEY"
    if is_whitelisted_key(candidate):
        return candidate
    return "CUSTOM_API_KEY"


def _write_secret(session: AsyncSession, env_name: str, value: str) -> None:
    """密钥写回 `.env`（白名单 + 原子写 + 备份）；审计只记键名与掩码，**绝不记明文**。"""
    if not is_whitelisted_key(env_name):
        raise BadRequestError(f"环境变量名 {env_name} 不在密钥白名单内")
    try:
        set_env_key(env_name, value)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    logger.info("llm_api_key_written", env_name=env_name, masked=mask(value))


async def _clear_other_defaults(session: AsyncSession, keep_pk: str | None) -> None:
    rows = (await session.execute(select(LlmModel).where(LlmModel.is_default.is_(True)))).scalars().all()
    for row in rows:
        if row.id != keep_pk:
            row.is_default = False
            row.updated_at = utcnow()
    await session.flush()


def _dump_chain(chain: list[str] | None, provider_id: str, model_id: str) -> str | None:
    """规范化降级链：去重、排除自身，空链存 NULL。"""
    if not chain:
        return None
    self_ref = f"{provider_id}:{model_id}"
    seen: list[str] = []
    for item in chain:
        if not isinstance(item, str):
            continue
        ref = item.strip()
        if not ref or ref == self_ref or ref in seen:
            continue
        seen.append(ref)
    return json.dumps(seen, ensure_ascii=False) if seen else None


async def default_model_ref(session: AsyncSession) -> str | None:
    """当前默认模型的 model_ref（无则 None）。"""
    row = (
        await session.execute(select(LlmModel).where(LlmModel.is_default.is_(True), LlmModel.enabled.is_(True)))
    ).scalars().first()
    if row is None:
        row = (
            await session.execute(select(LlmModel).where(LlmModel.enabled.is_(True)))
        ).scalars().first()
    return f"{row.provider_id}:{row.model_id}" if row is not None else None


async def all_model_refs(session: AsyncSession) -> list[dict[str, Any]]:
    """全部可用模型（前端「切换本次使用的模型」下拉）。"""
    rows = (
        (
            await session.execute(
                select(LlmModel, LlmProvider)
                .join(LlmProvider, LlmProvider.id == LlmModel.provider_id)
                .where(LlmModel.enabled.is_(True), LlmProvider.enabled.is_(True))
                .order_by(LlmModel.is_default.desc(), LlmProvider.priority.desc())
            )
        )
        .all()
    )
    return [
        {
            "model_ref": f"{p.id}:{m.model_id}",
            "display_name": m.display_name or m.model_id,
            "provider_name": p.name,
            "provider_type": p.type,
            "is_default": m.is_default,
            "max_context": m.max_context,
            "supports_tools": m.supports_tools,
        }
        for m, p in rows
    ]


async def chain_of(session: AsyncSession, model_ref: str) -> list[str]:
    """对外暴露降级链（供 UI 展示）。"""
    return await get_fallback_chain(session, model_ref)


def settings_snapshot() -> dict[str, Any]:
    """非敏感配置快照（供 /llm/overview 与前端展示）。"""
    from app.core.config import current_config_version

    s = get_settings()
    return {
        "config_version": current_config_version(),
        "sse_buffer_size": s.sse_buffer_size,
        "langfuse_enabled": s.langfuse_enabled,
        "provider_keys": _provider_key_overview(),
        "suggested_key_envs": list(SUGGESTED_KEY_ENVS),
        "provider_types": list(PROVIDER_TYPES),
    }
