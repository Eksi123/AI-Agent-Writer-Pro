#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.agent.tracing
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: Langfuse v5（OTel 版）集成（手册「三、(1) 可观测」）。

    v5 与 v2 的 API 完全不同：`Langfuse.trace()` / `trace.span()` **已不存在**
    （`demo-langfuse.py` 是 v2 写法，不可照抄）。本项目用法：

    - **自动埋点为主**：`langfuse.langchain.CallbackHandler` 挂到 Agent 调用的
      `config={"callbacks": [handler]}`，自动产出 trace/span/generation（含 token 用量）；
    - **手动补充**：`langfuse.start_as_current_observation(name=...)` 包裹门禁 G0/G1/G3 与
      `summarize_chapter` 等服务层 LLM 调用；
    - **未配置 Key 自动降级**：返回 None handler，功能不受影响（本地结构化日志已在 core/logging）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: Any = None
_init_attempted = False


def get_client() -> Any | None:
    """惰性初始化 Langfuse 客户端；未配置 Key 返回 None（自动降级）。"""
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True

    settings = get_settings()
    if not settings.langfuse_enabled:
        logger.info("langfuse_disabled_local_logs_only")
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        logger.info("langfuse_initialized", host=settings.langfuse_host)
    except Exception as exc:  # 追踪失败绝不影响写作功能
        logger.warning("langfuse_init_failed_degrade_to_local", error=str(exc))
        _client = None
    return _client


def reset_client() -> None:
    """`POST /config/reload` 后调用（Key 可能变了）。"""
    global _client, _init_attempted
    _client = None
    _init_attempted = False


def build_callback_handler(**metadata: Any) -> Any | None:
    """langchain CallbackHandler（挂到 `config={"callbacks": [...]}`）。"""
    client = get_client()
    if client is None:
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler(**{k: v for k, v in metadata.items() if v is not None})
    except Exception as exc:  # pragma: no cover
        logger.warning("langfuse_handler_failed", error=str(exc))
        return None


@contextmanager
def observation(name: str, *, metadata: dict[str, Any] | None = None) -> Iterator[Any]:
    """包裹服务层 LLM 调用（门禁预检/后检、摘要生成）。未配置时为 no-op。"""
    client = get_client()
    if client is None:
        yield None
        return
    try:
        with client.start_as_current_observation(name=name, metadata=metadata or {}) as obs:
            yield obs
    except Exception as exc:  # pragma: no cover - 追踪异常不外泄
        logger.warning("langfuse_observation_failed", name=name, error=str(exc))
        yield None


def create_score(name: str, value: Any, **kwargs: Any) -> None:
    """写分数（字数、工具成功率、是否降级、门禁判定分数）。失败静默。"""
    client = get_client()
    if client is None:
        return
    try:
        client.create_score(name=name, value=value, **kwargs)
    except Exception as exc:  # pragma: no cover
        logger.debug("langfuse_score_failed", name=name, error=str(exc))


def current_trace_id() -> str | None:
    """取当前 trace id（落 `agent_runs.langfuse_trace_id`）。"""
    client = get_client()
    if client is None:
        return None
    try:
        obs = client.get_current_observation()
        if obs is not None:
            return getattr(obs, "trace_id", None) or getattr(obs, "id", None)
        span = client.get_current_trace()
        return getattr(span, "id", None) if span is not None else None
    except Exception:  # pragma: no cover - v5 API 差异容错
        return None
