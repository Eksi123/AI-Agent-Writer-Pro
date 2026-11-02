# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.llm
文件名: __init__.py
作者: xionggaoqing
时间: 2026-11-02
说明: LLM 调用（架构优化 v1102，自 agent/llm 迁入 loop）：多厂商 ChatModel 工厂、
    四层参数覆盖、model_ref 解析。

    密钥只存 `.env`（DB 仅记 `api_key_env` 变量名）；`model_ref = "{provider_id}:{model_id}"`。
    ★容错降级/预算等安全措施在 `agent/harness/llm/`。
"""

from app.agent.loop.llm.factory import build_chat_model, resolve_model_ref
from app.agent.loop.llm.params import DEFAULT_PARAMS, merge_params

__all__ = ["build_chat_model", "resolve_model_ref", "DEFAULT_PARAMS", "merge_params"]
