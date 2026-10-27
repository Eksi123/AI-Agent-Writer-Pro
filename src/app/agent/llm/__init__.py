# -*- coding: utf-8 -*-
"""
模块: app.agent.llm
文件名: __init__.py
作者: xionggaoqing
时间: 2026-10-27
说明: LLM 接入：多厂商 ChatModel 工厂、四层参数覆盖、model_ref 解析、token 预算。

    密钥只存 `.env`（DB 仅记 `api_key_env` 变量名）；`model_ref = "{provider_id}:{model_id}"`。
"""

from app.agent.llm.factory import build_chat_model, resolve_model_ref
from app.agent.llm.params import DEFAULT_PARAMS, merge_params

__all__ = ["build_chat_model", "resolve_model_ref", "DEFAULT_PARAMS", "merge_params"]
