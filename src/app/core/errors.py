# -*- coding: utf-8 -*-
"""
模块: app.core.errors
文件名: errors.py
作者: xionggaoqing
时间: 2026-10-27
说明: 统一异常与错误码常量（见手册「六、API 约定 → 通用约定」）。

    前端据 `code` 做差异化提示；HTTP 状态码与 code 对齐。
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    """错误码常量。命名与手册「六」的错误码表逐行对应。"""

    OK = 0
    BAD_REQUEST = 40001
    FORBIDDEN = 40301
    NOT_FOUND = 40401
    VERSION_CONFLICT = 40901
    RUN_STATE_CONFLICT = 40902
    #: 章节正被 AI 编辑（内容锁），用户保存（含自动保存）一律拒绝
    CONTENT_LOCKED = 40903
    STREAM_BUFFER_LOST = 41001
    NAMING_INVALID = 42201
    OUTLINE_PARSE_FAILED = 42202
    INTERNAL_ERROR = 50001
    LLM_PROVIDER_FAILED = 50201
    DEPENDENCY_UNAVAILABLE = 50301


#: code → HTTP 状态码
CODE_TO_HTTP: dict[int, int] = {
    ErrorCode.OK: 200,
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.RUN_STATE_CONFLICT: 409,
    ErrorCode.CONTENT_LOCKED: 409,
    ErrorCode.STREAM_BUFFER_LOST: 410,
    ErrorCode.NAMING_INVALID: 422,
    ErrorCode.OUTLINE_PARSE_FAILED: 422,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.LLM_PROVIDER_FAILED: 502,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
}

#: code → 默认文案（可被 ApiError 的 message 覆盖）
CODE_TO_MESSAGE: dict[int, str] = {
    ErrorCode.OK: "success",
    ErrorCode.BAD_REQUEST: "参数格式错误",
    ErrorCode.FORBIDDEN: "越权访问被拒绝",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.VERSION_CONFLICT: "版本冲突，内容已被其它端修改",
    ErrorCode.RUN_STATE_CONFLICT: "运行状态冲突",
    ErrorCode.CONTENT_LOCKED: "AI编辑中，请稍后再试……",
    ErrorCode.STREAM_BUFFER_LOST: "stream buffer lost",
    ErrorCode.NAMING_INVALID: "显式命名校验失败（不允许空名/默认名/纯序号名）",
    ErrorCode.OUTLINE_PARSE_FAILED: "大纲解析失败（模板结构被破坏）",
    ErrorCode.INTERNAL_ERROR: "服务端内部错误",
    ErrorCode.LLM_PROVIDER_FAILED: "LLM Provider 调用失败",
    ErrorCode.DEPENDENCY_UNAVAILABLE: "依赖不可用",
}


class ApiError(Exception):
    """业务异常基类：携带 code / message / data，由全局异常处理器转成统一响应。

    `data` 用于回传补充信息，例如版本冲突时的最新内容与版本号。
    """

    code: int = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str | None = None,
        *,
        code: int | None = None,
        data: Any = None,
    ) -> None:
        self.code = code if code is not None else self.code
        self.message = message or CODE_TO_MESSAGE.get(self.code, "error")
        self.data = data
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return CODE_TO_HTTP.get(self.code, 500)


class BadRequestError(ApiError):
    code = ErrorCode.BAD_REQUEST


class ForbiddenError(ApiError):
    code = ErrorCode.FORBIDDEN


class NotFoundError(ApiError):
    code = ErrorCode.NOT_FOUND


class VersionConflictError(ApiError):
    """乐观锁失败（409 / 40901）。`data` 内含最新 content/version/updated_at/last_writer。"""

    code = ErrorCode.VERSION_CONFLICT


class ContentLockedError(ApiError):
    """章节正被 AI 编辑（409 / 40903）。用户保存（含自动保存）一律拒绝。"""

    code = ErrorCode.CONTENT_LOCKED


class NamingInvalidError(ApiError):
    """显式命名校验失败（422 / 42201）。"""

    code = ErrorCode.NAMING_INVALID


class OutlineParseError(ApiError):
    code = ErrorCode.OUTLINE_PARSE_FAILED


class DependencyUnavailableError(ApiError):
    """外部依赖缺失，如 reportlab 未安装、FTS5 不可用、MCP 探测失败（503 / 50301）。"""

    code = ErrorCode.DEPENDENCY_UNAVAILABLE


class LlmProviderError(ApiError):
    code = ErrorCode.LLM_PROVIDER_FAILED
