# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.netprobe
文件名: netprobe.py
作者: xionggaoqing
时间: 2026-11-08
说明: 网络 / 大模型接口连通性探测（run 失败后的 30s 宽限续跑用）。

    run 因网络或模型接口异常失败后，`runner._execute_run` 进入宽限循环：
    每 3s 调 `probe_provider` 一次，30s 内探测恢复则以 checkpoint 续跑原任务；
    超时则落库 notice（「当前网络异常…」/「当前大模型接口请求异常…」）。

    探测策略（轻量、只读）：
    - openai_compatible / openrouter / dashscope 等 OpenAI 兼容厂商：GET {base_url}/models；
    - 其余厂商（anthropic/google 等）：GET base_url 根路径，任意 HTTP 响应即视为网络可达
      （401/403 说明网络通、仅鉴权问题，仍判为「可达」以便续跑由模型层重试处理）。
    - 连接失败 / DNS 失败 / 超时 → 不可达。
"""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 单次探测超时（秒）——宽限循环每 3s 一轮，探测本身必须快进快出
PROBE_TIMEOUT = 2.5

#: OpenAI 兼容厂商类型（用 /models 端点探测）
_OPENAI_COMPATIBLE = {"openai_compatible", "openrouter", "dashscope", "openai"}


async def probe_provider(session: AsyncSession, model_ref: str | None) -> bool:
    """探测 model_ref 对应 provider 是否可达；解析失败/无 base_url 时保守返回 False。"""
    from app.agent.loop.llm.factory import _base_url_for, resolve_model_ref

    try:
        if not model_ref:
            return False
        provider, _model = await resolve_model_ref(session, model_ref)
    except Exception as exc:  # pragma: no cover - 解析失败视为不可达
        logger.debug("probe_resolve_failed", model_ref=model_ref, error=str(exc))
        return False

    base_url = _base_url_for(provider)
    if not base_url:
        return False
    ptype = (getattr(provider, "type", "") or "").lower()
    url = base_url.rstrip("/")
    if ptype in _OPENAI_COMPATIBLE:
        url = f"{url}/models"

    from app.core.security import get_api_key

    headers: dict[str, str] = {}
    if provider.api_key_env:
        value, _missing = get_api_key(provider.api_key_env)
        if value:
            headers["Authorization"] = f"Bearer {value}"

    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
        # 任意 HTTP 响应（含 4xx/5xx）= 网络可达；接口层问题由续跑后的模型重试处理
        reachable = resp.status_code < 500 or ptype not in _OPENAI_COMPATIBLE
        logger.info("provider_probed", model_ref=model_ref, url=url, status=resp.status_code, reachable=reachable)
        return bool(reachable)
    except Exception as exc:
        logger.info("provider_probe_failed", model_ref=model_ref, url=url, error=str(exc)[:200])
        return False


def classify_failure(exc: BaseException) -> str:
    """把 run 期异常归类为 'network' | 'llm' | 'other'（宽限续跑与 notice 文案用）。"""
    # 网络层：httpx 连接/DNS/超时类
    if isinstance(exc, httpx.TransportError):
        return "network"
    name = type(exc).__name__
    if name in ("APIConnectionError", "APITimeoutError", "ConnectionError", "TimeoutError"):
        return "network"
    # 模型接口层：langchain/openai 的 provider 异常与业务 LlmProviderError
    from app.core.errors import ApiError, ErrorCode

    if isinstance(exc, ApiError) and exc.code == ErrorCode.LLM_PROVIDER_FAILED:
        return "llm"
    if name in ("LlmProviderError",) or name.endswith("APIStatusError") or name.startswith("OpenAI"):
        return "llm"
    msg = str(exc).lower()
    if any(k in msg for k in ("connection", "timeout", "timed out", "dns", "network", "unreachable")):
        return "network"
    if any(k in msg for k in ("rate limit", "429", "500", "502", "503", "provider", "api key", "authentication")):
        return "llm"
    return "other"


def notice_text_for(kind: str, error_text: str | None = None) -> str:
    """收尾 notice 文案（落库 kind=notice 的 assistant 消息）。"""
    if kind == "cancelled":
        return "当前任务已暂停"
    if kind == "network":
        return "当前网络异常，请稍后再试"
    reason = (error_text or "未知错误")[:300]
    return (
        f"当前大模型接口请求异常：{reason}。"
        "建议：检查模型配置与供应商服务状态后，重新发送消息重试。"
    )


__all__ = ["probe_provider", "classify_failure", "notice_text_for", "PROBE_TIMEOUT"]
