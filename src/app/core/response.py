#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块: app.core.response
文件名: response.py
作者: xionggaoqing
时间: 2026-10-27
说明: 统一响应包装 `{code, message, data}`（见手册「六、通用约定」）。

    路由直接返回 `ok(...)` / `err(...)` 构造的 dict，由 FastAPI 序列化为
    `{code, message, data}`；异常经 `main._register_exception_handlers` 转成同构信封。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import CODE_TO_HTTP, ErrorCode


def ok(data: Any = None, message: str = "success") -> dict[str, Any]:
    """构造成功响应体。"""
    return {"code": ErrorCode.OK, "message": message, "data": data}


def err(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """构造失败响应体。"""
    return {"code": code, "message": message, "data": data}


def http_status_for(code: int) -> int:
    """code → HTTP 状态码，未知 code 归 500。"""
    return CODE_TO_HTTP.get(code, 500)
