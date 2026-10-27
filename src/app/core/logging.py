# -*- coding: utf-8 -*-
"""
模块: app.core.logging
文件名: logging.py
作者: xionggaoqing
时间: 2026-10-27
说明: 结构化日志：structlog 输出 JSONL 到 `data/logs/`，含敏感字段脱敏（手册「三、(1)」、铁律 11）。
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from app.core.paths import logs_root

#: 键名匹配这些模式的字段一律脱敏（只留掩码，不记明文）
_SENSITIVE_KEY = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.IGNORECASE)
_CONFIGURED = False


def mask_secret(value: Any) -> str:
    """`sk-abcdefgh1234` → `sk-****1234`；过短则全掩码。"""
    if not isinstance(value, str) or not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}****{value[-4:]}"


def _mask_sensitive(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor：对键名敏感的字段做掩码。"""
    for key in list(event_dict.keys()):
        if _SENSITIVE_KEY.search(key):
            event_dict[key] = mask_secret(event_dict.get(key))
    return event_dict


def _utc_timestamp(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """时间戳统一 UTC ISO-8601（铁律 11）。"""
    event_dict["timestamp"] = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return event_dict


def _log_file(name: str) -> Path:
    """按日期轮转的文件名，如 `app-2026-10-26.jsonl`。"""
    return logs_root() / f"{name}-{datetime.now(UTC).strftime('%Y-%m-%d')}.jsonl"


def _jsonl_handler(filename: str) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        _log_file(filename), maxBytes=20 * 1024 * 1024, backupCount=7, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def configure_logging(level: str = "INFO") -> None:
    """幂等初始化：控制台可读输出 + `app-*.jsonl` / `error-*.jsonl`。

    审计日志走独立通道（`app.core.audit` / services），同时入 SQLite。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _utc_timestamp,
        _mask_sensitive,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.processors.JSONRenderer(ensure_ascii=False)],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # stdlib logging → JSONL 文件（uvicorn / sqlalchemy / httpx 等第三方也归档）
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(console)

    app_handler = _jsonl_handler("app")
    app_handler.setLevel(log_level)
    root.addHandler(app_handler)

    err_handler = _jsonl_handler("error")
    err_handler.setLevel(logging.ERROR)
    root.addHandler(err_handler)

    for noisy in ("uvicorn.access", "httpx", "httpcore", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """取一个结构化 logger。"""
    return structlog.get_logger(name) if name else structlog.get_logger()
