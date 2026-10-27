# -*- coding: utf-8 -*-
"""
模块: app.api.llm
文件名: llm.py
作者: xionggaoqing
时间: 2026-10-27
说明: LLM Provider / Model 管理 API（手册「六 → 路由树 · LLM」）。

        GET|POST       /llm/providers
        PATCH|DELETE   /llm/providers/{id}
        POST           /llm/providers/{id}/test     ★连接测试（极小请求）
        GET|POST|PATCH /llm/models                  （含 fallback_chain 配置）
        DELETE         /llm/models/{id}
        POST           /config/reload               ★重读 .env + 使 Agent 工厂缓存失效

    密钥只写 `.env`（DB 仅存 `api_key_env`），响应一律掩码。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_session
from app.core.response import ok
from app.schemas import (
    LlmModelCreate,
    LlmModelUpdate,
    LlmProviderCreate,
    LlmProviderTestIn,
    LlmProviderUpdate,
)
from app.services import llm as llm_service

router = APIRouter(tags=["llm"])


@router.get("/llm/overview")
async def overview(session: AsyncSession = Depends(get_session)) -> dict:
    """配置总览：默认模型、可用模型下拉、非敏感设置快照。"""
    return ok(
        {
            "settings": llm_service.settings_snapshot(),
            "default_model_ref": await llm_service.default_model_ref(session),
            "models": await llm_service.all_model_refs(session),
        }
    )


@router.get("/llm/providers")
async def list_providers(session: AsyncSession = Depends(get_session)) -> dict:
    """Provider 列表（含各自模型与密钥掩码状态）。"""
    return ok(await llm_service.list_providers(session))


@router.post("/llm/providers")
async def create_provider(body: LlmProviderCreate, session: AsyncSession = Depends(get_session)) -> dict:
    """新建 Provider。给了 `api_key_value` 则写入 `.env`（DB 只记变量名）。"""
    dto = await llm_service.create_provider(
        session,
        name=body.name,
        type=body.type,
        base_url=body.base_url,
        api_key_env=body.api_key_env,
        api_key_value=body.api_key_value,
        extra=body.extra,
        enabled=body.enabled,
        priority=body.priority,
    )
    await session.commit()
    return ok(dto)


@router.get("/llm/providers/{provider_id}")
async def get_provider(provider_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    providers = await llm_service.list_providers(session)
    for p in providers:
        if p["id"] == provider_id:
            return ok(p)
    from app.core.errors import NotFoundError

    raise NotFoundError(f"Provider 不存在：{provider_id}")


@router.patch("/llm/providers/{provider_id}")
async def update_provider(
    provider_id: str, body: LlmProviderUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    """更新 Provider（保存密钥后前端应调 /config/reload 或依赖本接口的缓存失效）。"""
    dto = await llm_service.update_provider(
        session,
        provider_id,
        name=body.name,
        type=body.type,
        base_url=body.base_url,
        api_key_env=body.api_key_env,
        api_key_value=body.api_key_value,
        clear_api_key=body.clear_api_key,
        extra=body.extra,
        enabled=body.enabled,
        priority=body.priority,
    )
    await session.commit()
    return ok(dto)


@router.delete("/llm/providers/{provider_id}")
async def delete_provider(provider_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """删除 Provider（级联删除其模型）。"""
    dto = await llm_service.delete_provider(session, provider_id)
    await session.commit()
    return ok(dto)


@router.post("/llm/providers/{provider_id}/test")
async def test_provider(
    provider_id: str,
    body: LlmProviderTestIn | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """连接测试（极小请求）。`ok=false` 时 message 给可读原因，HTTP 仍 200（非服务端错误）。"""
    dto = await llm_service.test_provider(session, provider_id, model_id=body.model_id if body else None)
    await session.commit()
    return ok(dto)


@router.get("/llm/models")
async def list_models(provider_id: str | None = None, session: AsyncSession = Depends(get_session)) -> dict:
    return ok(await llm_service.list_models(session, provider_id=provider_id))


@router.post("/llm/models")
async def create_model(body: LlmModelCreate, session: AsyncSession = Depends(get_session)) -> dict:
    dto = await llm_service.create_model(
        session,
        provider_id=body.provider_id,
        model_id=body.model_id,
        display_name=body.display_name,
        max_context=body.max_context,
        supports_tools=body.supports_tools,
        default_params=body.default_params,
        is_default=body.is_default,
        enabled=body.enabled,
        fallback_chain=body.fallback_chain,
    )
    await session.commit()
    return ok(dto)


@router.patch("/llm/models/{model_pk}")
async def update_model(
    model_pk: str, body: LlmModelUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    dto = await llm_service.update_model(
        session,
        model_pk,
        model_id=body.model_id,
        display_name=body.display_name,
        max_context=body.max_context,
        supports_tools=body.supports_tools,
        default_params=body.default_params,
        is_default=body.is_default,
        enabled=body.enabled,
        fallback_chain=body.fallback_chain,
    )
    await session.commit()
    return ok(dto)


@router.delete("/llm/models/{model_pk}")
async def delete_model(model_pk: str, session: AsyncSession = Depends(get_session)) -> dict:
    dto = await llm_service.delete_model(session, model_pk)
    await session.commit()
    return ok(dto)


@router.get("/llm/models/{model_pk}/fallback-chain")
async def fallback_chain(model_pk: str, session: AsyncSession = Depends(get_session)) -> dict:
    """降级链（有序 model_ref）。"""
    from app.db.models import LlmModel

    row: Any = await session.get(LlmModel, model_pk)
    if row is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(f"模型不存在：{model_pk}")
    chain = await llm_service.chain_of(session, f"{row.provider_id}:{row.model_id}")
    return ok({"model_pk": model_pk, "chain": chain})


@router.post("/config/reload")
async def reload_config(session: AsyncSession = Depends(get_session)) -> dict:
    """重读 `.env` 并使 Agent 工厂缓存失效（config_version += 1）。"""
    dto = await llm_service.reload_config(session)
    await session.commit()
    return ok(dto)
