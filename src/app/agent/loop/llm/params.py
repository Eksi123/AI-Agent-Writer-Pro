# -*- coding: utf-8 -*-
"""
模块: app.agent.loop.llm.params
文件名: params.py
作者: xionggaoqing
时间: 2026-10-27
说明: 四层参数覆盖（手册「二、(1) 参数设置」）。

    优先级（后者覆盖前者，未设置项逐层回落）：
    1. 全局默认（`.env` + `llm_models.default_params_json`）
    2. 小说项目级（`novel.yaml.llm_params`）
    3. 会话级（`agent_sessions.model_ref` 等）
    4. 单条消息级（发消息 body 内 `model_ref` / params 覆盖）

    只保留已显式设置的项，None 值不覆盖下层。
"""

from __future__ import annotations

from typing import Any

#: 允许的模型参数键（其余键忽略，避免把 model_ref 等误传进 ChatModel）
DEFAULT_PARAMS: dict[str, Any] = {
    "temperature": 0.8,
    "max_tokens": 4096,
    "top_p": 1.0,
    "timeout": 120,
    "max_retries": 2,
    "streaming": True,
}

_PARAM_KEYS = ("temperature", "max_tokens", "top_p", "timeout", "max_retries", "streaming", "extra_body")


def merge_params(*layers: dict[str, Any] | None) -> dict[str, Any]:
    """按顺序合并多层参数（后层覆盖前层），只取白名单键、跳过 None。"""
    merged: dict[str, Any] = dict(DEFAULT_PARAMS)
    for layer in layers:
        if not layer:
            continue
        for key in _PARAM_KEYS:
            if key in layer and layer[key] is not None:
                merged[key] = layer[key]
    return merged
